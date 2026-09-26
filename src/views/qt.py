"""The PySide6 frontend: the Bench sheet, tiered (owner ruling 2026-09-25).

Replaces `legacy/src/views/pyside/view.py` (1368 lines), `style.qss`, and the PySide
launcher plus its nested setup wizard in `legacy/src/app.py`.

What this module is allowed to know
-----------------------------------
The Controller, and — for the Setup panel only — a `Panel` handed to it. No
model import, no `setattr` on a model, no model-specific subclass.
`RedPercentDynamicView`, `PlotDialog` and `ControllerLogWindow` are gone: a
live series is a `plot` element, a saved run is the model's business, and the
gamepad log is a `log_stream` element that all three frontends already show.

Toolkit-neutral logic lives in `views.base`; this module supplies
widgets. `PanelView.__init__` refuses to construct unless a `_make_<type>`
exists for every `schema.ELEMENT_TYPES` entry, so "PySide cannot render that"
is a construction error rather than a silent blank row.

Multiple inheritance
--------------------
`QtPanelView(PanelView, QWidget)` and `QtDashboard(Dashboard, QMainWindow)`.
The toolkit-neutral class comes first so its logic wins on name collisions,
and each `__init__` calls the Qt base explicitly *first* (a Qt object must
exist before a signal is connected or a child widget is parented). `close()`
exists on both sides of both pairs, so both are overridden explicitly rather
than left to the MRO.

The window (docs/rebuild/DESIGN_BRIEF.md, canvas row E)
-------------------------------------------------------
A paper sheet, not a console. On the left, the **rail**: the title and the
simulation line, A's stop disc (always red; "Clear" and a thicker ring when
latched), "Stop: Ctrl+." under it, the model list (the opened model
highlighted; a press brings it to the top of the sheet), Setup and Quit at
the foot. To its right, the **sheet**: one *entry* per open model - a 2 px
ink rule, the model's name, its tier-1 body - with no card edges; the opened
model full width on top, the rest in rows of three and two, as many columns
as fit without clipping a number. Setup is a dock above the sheet; the event
tray is one line under it and reports warnings and errors only.

Tiers (schema `section(..., tier=)`)
------------------------------------
Tier 1 is always drawn. Tier 2 sits behind ONE disclosure per model
("Configure" / "Details", the section's own `disclosure` text) in a panel-toned
well; tier 3 behind a second disclosure ("Diagnostics") inside that well, a
strip with a muted left rule. Open state is remembered per model for the
session (`QtPanelView.open_tiers`). A readonly whose value is normal
(`theme.QUIET_VALUES`) is not drawn in tier 1: status by exception.

Layout inside a section
-----------------------
`schema.section(..., layout=)` is a hint every renderer has to honour.
`_make_section` returns one of two containers with a single `add`/`add_wide`
API — `FlowSection` (captioned controls that wrap like words, so a number
is never clipped: it moves to the next line) or a `TableRow` in the panel's
shared `PanelTable` — so the element builders carry no layout branch. A row
section's columns are allocated by *label*, which is what lines "Port" up
under "Port" across models that do not all declare the same controls. Setup
is the panel this exists for (Addendum 2).

Styling
-------
Only through `views.theme`. `stylesheet()` generates the whole sheet from
theme values, which is why there is no `.qss` file, no colour literal and no
pixel font size anywhere below. The sheet goes on the QApplication, not the
window, so message boxes and file dialogs are themed too (PYSIDE-15). The
stop disc, the per-model switch and the tick glyph paint themselves from the
same theme values.

Threads
-------
Events arrive on whatever thread published them. `_marshal` hands every one
to the GUI thread through a signal connected with `Qt.QueuedConnection` —
never `AutoConnection`, which resolves to a *direct* call when the publisher
is already on the GUI thread and so opened a modal inside the publisher's own
call stack, mid-teardown (PYSIDE-21). `base.Dashboard` additionally refuses to
open a popup while `_closing`; that guard is load-bearing and is kept.
"""
import html
import math
import os
import re
import shutil
import sys
import time

import schema as sch
from events import events
from views import theme
from views.base import Dashboard, PanelView, stop_words

try:                                    # the module imports without PySide6
    from PySide6.QtCore import (QEasingCurve, QEvent, QEventLoop, QLocale,
                                QPoint, QPointF, QRect, QRectF, QSize, Qt,
                                QTimer, QUrl, QVariantAnimation, Signal)
    from PySide6.QtGui import (QColor, QDoubleValidator, QFont,
                               QFontDatabase, QFontMetrics, QGuiApplication,
                               QIcon, QImage,
                               QIntValidator, QKeySequence, QPainter,
                               QPainterPath, QPen, QPixmap, QShortcut,
                               QTextCursor, QTextDocument)
    from PySide6.QtWidgets import (
        QAbstractButton, QApplication, QCheckBox, QComboBox, QDialog,
        QDockWidget, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
        QLayout, QLineEdit, QMainWindow, QMessageBox, QPushButton,
        QScrollArea, QSizePolicy, QSlider, QStyle, QStyleOptionButton,
        QStyleOptionComboBox, QStylePainter, QTextEdit, QToolButton,
        QVBoxLayout, QWidget)
    HAS_QT = True
except ImportError:                     # pragma: no cover - exercised by test
    HAS_QT = False

    def Signal(*_args, **_kwargs):
        """Placeholder so a class body declaring a signal still evaluates."""
        return None

    class _NoQt:
        """Stands in for every Qt base class when PySide6 is not installed.

        The module still imports — the pure helpers below are what the non-Qt
        tests exercise — but nothing can be *built*, and says so.
        """

        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("PySide6 is not installed: the Qt view cannot "
                               "be built. Launch with the Tk or Web view.")

    QWidget = QMainWindow = QDockWidget = QPushButton = QLabel = _NoQt
    QComboBox = QFrame = QCheckBox = QDialog = QAbstractButton = _NoQt
    QLayout = QSlider = _NoQt


#: A drag smaller than this in either axis is a stray click, not a region.
MIN_REGION_PX = 10

#: Decimals a numeric entry's *validator* accepts, regardless of the decimals
#: the parameter *displays*. PYSIDE-19: the old validator was fixed at 3, so
#: a PID integral term of 0.0005 could not be typed at all. Display precision
#: and input precision are different questions; `Param.parse` is the gate that
#: decides whether a typed value is acceptable, and it runs in the model.
INPUT_DECIMALS = 9

#: What an `int` entry's validator falls back to when the parameter declares
#: no bound. `QIntValidator` takes C ints, so `validator_bounds`'s 1e12 cannot
#: be handed to it at all.
INT_LIMIT = 2 ** 31 - 1

# -- geometry. Spacing comes from `theme.SPACE` and the PAD/GAP/INSET names;
# what is left here is the handful of sizes the theme has no opinion about,
# and those are measured from the font wherever the thing they size holds
# text.
#: Every table column that holds a control, so a row with no gamepad still
#: lines up with one that has one (Setup's table).
TABLE_CONTROL_MIN_PX = 150
#: A dropdown sizes to this many characters rather than to its longest option,
#: which is what makes a column of them one width instead of four. A longer
#: name is elided from the middle - "/dev/cu.usbm…4401" - because the end of
#: a port or gamepad name is the part that tells two devices apart (F15).
DROPDOWN_CHARS = 14
DROPDOWN_ROW_CHARS = 10
#: A numeric entry is this many digits wide, a text entry this many letters:
#: a speed does not need an 800 px well.
NUMBER_CHARS, TEXT_CHARS = 7, 14
#: A speed slider's travel, in characters of the base font (beside its entry).
SLIDER_CHARS = 16
#: An `indicator` is a lamp: a dot beside its label, never a full-width box.
#: This is its floor; above 12 pt it follows the font (`lamp_px`, AUD-12).
LAMP_PX = 16
#: A `log_stream` is a few scrollable lines, fixed. Left to expand, the
#: stepper's Gamepad Log took a third of the panel.
LOG_STREAM_PX = 110
#: A detached stream's window (G4) opens this many characters wide and lines
#: tall, measured from its font; the operator can resize it.
DETACHED_LOG_CHARS, DETACHED_LOG_LINES = 72, 18
#: The event tray's open height, and the number of lines it keeps. Closed, it
#: is one line: the latest warning or error.
EVENT_LOG_PX, EVENT_LOG_LINES = 150, 500
#: The rail: 248 px, 200 px when the window is under 1000 px wide (the
#: brief's Layout). It grows with the launch font, up to this factor, so a
#: 28 pt model name still has room.
RAIL_PX, RAIL_NARROW_PX, NARROW_WINDOW_PX = 248, 200, 1000
RAIL_GROWTH = 1.3
#: The type scale is `theme.size(step)`: -1 a caption, 0 the base, 1 a
#: model's name or a statistic, 2 the Setup heading.
STEP_CAPTION, STEP_BASE, STEP_READOUT, STEP_TITLE = -1, 0, 1, 2
#: The pulse when the latch closes: once, on the edge, never while it stays.
#: `STATION_NO_MOTION=1` turns it off (`motion_reduced`); the face and the
#: ring still change, so the state still shows (AUD-14).
PULSE_MS = 400
DANGER_ROLE = "danger"
#: The global stop's keyboard shortcut, as the operator reads it: Control
#: and period, the same two keys on macOS, Windows and Linux (owner ruling
#: 2026-09-25, G5: no platform-specific UI). It only ever stops; clearing
#: stays a deliberate press of the face, and a question (F9).
STOP_SHORTCUT_TEXT = "Ctrl+."
#: The same keys in Qt's spelling. Qt names the macOS Command key "Ctrl" and
#: the macOS Control key "Meta", so the physical Control+period is "Meta+." on
#: macOS and "Ctrl+." everywhere else. A naming quirk the toolkit forces, not
#: a second chord: one binding, the same keys pressed on every OS, and no
#: Command binding anywhere. (`AA_MacDontSwapCtrlAndMeta` would remove the
#: branch but also move Copy/Paste in every text field off the Command key.)
STOP_SHORTCUT = "Meta+." if sys.platform == "darwin" else "Ctrl+."
#: The stop's two names, the Web and Tk views' words: what a press does. The
#: line under the disc names the chord (the artboard's "Stop: Ctrl+.").
STOP_HINT, CLEAR_HINT = "Stop every model", "Clear the stop on every model"
RAIL_STOP_HINT = f"Stop: {STOP_SHORTCUT_TEXT}"
#: At most this many readings of one model are drawn at reading size (the
#: ones it flags `rail=True`; else the first section's readouts).
RAIL_READOUTS = 4
#: A panel readout wider than this many characters is elided from the middle
#: with the whole value as its tooltip, so a 60-character Run ID cannot push
#: the column sideways (F15). A number never comes near it.
READOUT_MAX_CHARS = 28
#: A number that changed within this many seconds is drawn in the trace; at
#: rest it is ink (the brief: trace is for changing numbers only).
LIVE_S = 1.0
#: Readings follow the launch font up to this factor: at 28 pt a 52 px
#: focal number would otherwise be 121 px.
READING_GROWTH = 1.6
#: L4: the floor for anything pressable (WCAG 2.5.8) and for a command.
TARGET_PX, COMMAND_PX = 24, 36
#: At most three entries side by side, 44 px apart (`design-Sheet.md`).
MAX_SHEET_COLUMNS = 3
SHEET_GUTTER = theme.SPACE[10]
#: An entry head's edge: transparent at rest, the focus ring when the head
#: (a press target on the overview) has keyboard focus.
HEAD_EDGE_PX = 2
#: Keyboard focus: two pixels of ink on every control (F25). Never trace.
FOCUS_RING = f"2px solid {theme.STOP_FOCUS}"
#: A readout whose value is one of these is at rest, not live: it is drawn in
#: the muted ink, so "off" never competes with a number that is moving. (In
#: tier 1 a value in `theme.QUIET_VALUES` is not drawn at all.)
QUIET_VALUES = frozenset({"", "off", "none", "false", "no", "--",
                          "nothing selected", "not scanned", "not scanned yet"})
#: What an empty readout shows: a dash, not a blank that reads as broken.
EMPTY_READOUT = "—"
#: The empty state: what to do next, not "nothing here".
EMPTY_TITLE = "No instruments running."
EMPTY_HINT = "Choose ports in Setup and press Launch."
#: The rail's simulation line, and the sheet's latched headline (the
#: artboards' copy).
SIM_LINE = "Simulation, no hardware attached"
UNCONFIRMED_LINE = "Stop not confirmed. Treat as live."
#: L1: a model's state on the rail, beside its name. The words go in its
#: tooltip and accessible name (never colour alone); the square's colour is
#: ink for a latched model, the signal for one that did not confirm.
RAIL_STOP_WORDS = {"stopped": "stopped", "unconfirmed": "did not confirm"}
#: L2: the Controller's event title for a stop that did not confirm. The
#: band drops it once the latch opens; while latched it waits for its
#: acknowledgement like any error.
UNCONFIRMED_TITLE = "Stop Not Confirmed"
#: L9 / L14: the questions, titled, answered by verbs - the Tk and Web words.
QUIT_PROMPT = ("Quit the station? This stops every model, closes every port "
               "and exits.")
QUIT_WORDS = ("Quit the station?", "Quit", "Stay")
CLEAR_WORDS = ("Clear the stop?", "Clear the stop", "Keep it stopped")
CONFIRM_WORDS = ("Confirm", "Continue", "Cancel")
#: The sheet's two pages (K4): the rail's first item, and the word at the
#: right of an overview entry's head (a press opens the device alone).
OVERVIEW = "Overview"
OPEN_WORD = "Open"
#: L15: what an image pane says while the model has no picture for it, when
#: the schema declares no `empty` of its own (the Web view's words).
FIGURE_EMPTY = {"figure": "No run loaded. Load run opens a saved CSV and "
                          "plots it here."}
#: A unit as the operator reads it beside a number.
UNIT_WORDS = {"C": "°C", "s/C": "s/°C"}


# ---------------------------------------------------------------------------
# Pure functions. No Qt, no widgets, unit-tested without a QApplication.
# ---------------------------------------------------------------------------

def _rule(selector, declarations):
    body = "".join(f"    {name}: {value};\n" for name, value in declarations.items())
    return f"{selector} {{\n{body}}}\n"


def qt_alive(obj):
    """False once the C++ side of a Qt object has been deleted (its parent
    went first). Python still holds the wrapper; touching it would raise."""
    try:
        import shiboken6
        return bool(shiboken6.isValid(obj))
    except ImportError:                 # pragma: no cover - no PySide6
        return obj is not None


def motion_reduced():
    """The latch pulse is the only motion this view makes; `STATION_NO_MOTION=1`
    (or any value but 0) turns it off. `app.py` has no `--no-motion` flag to
    read yet (a core change request), so the environment carries it."""
    return os.environ.get("STATION_NO_MOTION", "").strip() not in ("", "0")


def device_word(kind):
    """A device's type name as the operator would say it: "SerialPort" ->
    "serial port". An all-capitals name (SMC100) is a product, kept."""
    kind = str(kind or "device")
    if re.fullmatch(r"[A-Z0-9]+", kind):
        return kind
    return re.sub(r"(?<!^)(?=[A-Z])", " ", kind).lower()


def lost_devices(state):
    """The devices a model's state reports as lost, in operator words. A model
    publishes `devices: {"SerialPort": "lost"}` when its link fails (HC-1);
    before this, no view read it and a dead probe looked live."""
    devices = (state or {}).get("devices") or {}
    return [device_word(kind) for kind, status in devices.items()
            if str(status).lower() == "lost"]


def lost_sentence(name, lost):
    """What the rail and the entry say about a lost device."""
    return f"{name} lost its {' and '.join(lost)}"


def simulation_line(states):
    """The rail's line under the title: the simulation sentence when the open
    models' ports are simulated and none is verified hardware, the simulated
    models' names when only some are, and nothing when the hardware is real
    (the brief: nothing when connected). A gamepad or a screen is neither."""
    simulated, real = [], []
    for name, state in (states or {}).items():
        statuses = {str(s).lower() for s in ((state or {}).get("devices") or {}).values()}
        if "simulated" in statuses:
            simulated.append(name)
        elif "verified" in statuses:
            real.append(name)
    if simulated and not real:
        return SIM_LINE
    if simulated:
        return "Simulated: " + ", ".join(simulated)
    return ""


def lamp_colours(element, is_on):
    """(fill, ring) of a lamp - Tk's rule, so the two views agree (AUD-3).

    Lit is ink, or signal when being on is a fault; the trace is spent on
    changing numbers only (the Bench sheet), so a lamp never borrows it. Unlit
    is a muted ring; unlit where *off* is the fault (a disconnected stage) is
    an ink ring - louder than muted, never a second red.
    """
    if is_on:
        lit = theme.SIGNAL if element.get("on_role") == DANGER_ROLE else theme.TEXT
        return lit, lit
    ring = theme.TEXT if element.get("off_role") == DANGER_ROLE else theme.MUTED
    return "transparent", ring


def sentence(text):
    """Sentence case for copy: a SHOUTED word of four letters or more comes
    down ("FULL STOP" -> "Full stop") while "Sync X: OFF" keeps the capitals
    that carry meaning. The Web view's `sentence`, word for word. The schema
    is the model's vocabulary and is not edited; this is presentation."""
    words = [word.lower() if re.fullmatch(r"[A-Z]{4,}[:.,]?", word) else word
             for word in str("" if text is None else text).split(" ")]
    joined = " ".join(words)
    return joined[:1].upper() + joined[1:] if joined else joined


def sentence_case(text):
    """Sentence case for a label or a heading (the Web view's `sentenceCase`):
    interior Capitalised words come down too - "Probe Tilt Angle:" reads
    "Probe tilt angle" - while an initialism, an axis letter or a unit is left
    as the model wrote it. The trailing colon goes: the value sits beside the
    label on a panel, not after it in a form."""
    words = re.sub(r"\s*:\s*$", "", sentence(text)).split(" ")
    return " ".join(word.lower() if index and re.fullmatch(r"[A-Z][a-z]+", word)
                    else word for index, word in enumerate(words))


def quiet_shouting(text):
    """A SHOUTED word of four letters or more comes down ("FULL STOP" ->
    "full stop"); nothing else changes, so a device's reply keeps its case."""
    return " ".join(word.lower() if re.fullmatch(r"[A-Z]{4,}[:.,]?", word) else word
                    for word in str("" if text is None else text).split(" "))


def event_line(event):
    """An event as the operator reads it (L11): its title in sentence case,
    then its message - no `[source]` prefix, no Title Case, no shouting.
    Something that is not an `events.Event` (no title) keeps its text."""
    title = str(getattr(event, "title", "") or "").strip()
    message = " ".join(str(getattr(event, "message", "") or "").split())
    if not title:
        text = " ".join(str(getattr(event, "text", "") or message).split())
    else:
        title = sentence_case(title)
        text = f"{title}: {quiet_shouting(message)}" if message else title
    count = int(getattr(event, "count", 1) or 1)
    if count > 1:
        text += f" (repeated {count} times)"
    return text


#: L3: why a command is greyed out, in the operator's words: the gate token
#: that refused it (`disabled_when`), or what an `enabled_when` command is
#: waiting for. One map, three views (brief-l-views.md).
GATE_WORDS = {"latched": "Stopped: clear the stop first",
              "manual": "In manual mode",
              "running": "A run is in progress",
              "no_region": "Set a capture region first",
              "disconnected": "Not connected",
              "moving": "Moving"}
WAITING_WORDS = {"launched": "Nothing launched yet",
                 "running": "No run in progress",
                 "manual": "Not in manual mode",
                 "connected": "Not connected"}


def gate_reason(element, mode, values=None, caption_of=None):
    """Why `schema.is_enabled` refuses `element` in `mode`, or "" when it
    does not. `caption_of(attr)` names an `enabled_by` switch."""
    mode = str(mode or "")
    by = element.get("enabled_by")
    if by and values is not None and not values.get(by):
        name = caption_of(by) if caption_of else ""
        return f"Tick {name} first" if name else "Not selected"
    if mode in (element.get("disabled_when") or ()):
        return GATE_WORDS.get(mode) or sentence(mode.replace("_", " "))
    enabled = element.get("enabled_when")
    if enabled and mode not in enabled:
        if mode in GATE_WORDS and mode in ("latched", "disconnected"):
            return GATE_WORDS[mode]
        if len(enabled) == 1 and enabled[0] in WAITING_WORDS:
            return WAITING_WORDS[enabled[0]]
        return "Only while " + " or ".join(str(m).replace("_", " ") for m in enabled)
    return ""


def split_unit(text, unit=None):
    """(caption, unit) for a label: "Position (deg):" -> ("Position", "deg").
    A declared `unit` wins; a trailing parenthesis that reads as a unit (no
    spaces, not a capitalised word - "Brake Speed (Slow)" keeps its word) is
    the unit otherwise, so the number carries its unit beside it."""
    raw = re.sub(r"\s*:\s*$", "", str("" if text is None else text))
    match = re.fullmatch(r"(.*?)\s*\(([A-Za-z%/\u00b0]{1,8})\)", raw)
    if match and not re.fullmatch(r"[A-Z][a-z]+", match.group(2)):
        raw, unit = match.group(1), unit or match.group(2)
    unit = UNIT_WORDS.get(unit, unit) if unit else ""
    return sentence_case(raw), unit


def axis_letter(element):
    """"X:" -> "X": a readout named by one capital letter is an axis, drawn as
    an inline letter beside its number under one caption per section."""
    text = re.sub(r"\s*:\s*$", "", str(element.get("text") or "")).strip()
    return text if re.fullmatch(r"[A-Z]", text) else None


def as_operator_word(text):
    """A boolean readout is shown as the operator would say it ("Yes" / "No"),
    not as a programming literal - the Web view's rule (`app.js`), so the two
    views read the same."""
    if isinstance(text, bool):
        return "Yes" if text else "No"
    if isinstance(text, str) and text.strip().lower() in ("true", "false"):
        return "Yes" if text.strip().lower() == "true" else "No"
    return text


def is_number(text):
    """A reading's text is a number ("1 184", "-352", "12.50", "1e-3")."""
    return bool(re.fullmatch(r"[-+\u2212]?\d[\d,\u2009 ]*(\.\d*)?([eE][-+]?\d+)?",
                             str("" if text is None else text).strip()))


def is_quiet_value(text):
    """A readout at rest ("off", "False", nothing) rather than a live one."""
    return str("" if text is None else text).strip().lower() in QUIET_VALUES


def is_normal_value(text):
    """Status by exception: a value the brief calls normal is not drawn in
    tier 1 ("Connected", "Idle", "No", "None", "--", "Not recording", empty)."""
    word = as_operator_word(text)
    return (str("" if text is None else text).strip() in theme.QUIET_VALUES
            or str("" if word is None else word).strip() in theme.QUIET_VALUES)


def rail_elements(schema):
    """The readings a model draws at reading size (the Web view's
    `railElements`): the readouts it flags `rail=True`, else the readonly
    elements of its first section that has any. Derived, never named, so a
    model this file has never heard of still shows its numbers."""
    sections = (schema or {}).get("sections") or []
    flagged = [e for s in sections for e in s.get("elements") or ()
               if e.get("type") == "readonly" and e.get("model_attr")
               and e.get("rail")]
    if flagged:
        return flagged[:RAIL_READOUTS]
    for section in sections:
        readouts = [e for e in section.get("elements") or ()
                    if e.get("type") == "readonly" and e.get("model_attr")]
        if readouts:
            return readouts[:RAIL_READOUTS]
    return []


def reading_kinds(schema):
    """{id(element): "axis" | "primary" | "secondary"} for a model's rail
    readings in tier 1. Axis letters are `focal` on the opened model and
    `compact` on the others; a single-value model's number is `primary`, a
    second one (a change) `secondary` (`theme.READING_SIZES`)."""
    sections = (schema or {}).get("sections") or []
    tier_one = {id(e) for s in sections if s.get("tier", 1) == 1
                for e in s.get("elements") or ()}
    kinds, singles = {}, 0
    for element in rail_elements(schema):
        if id(element) not in tier_one or not element.get("rail"):
            continue
        if axis_letter(element):
            kinds[id(element)] = "axis"
        else:
            kinds[id(element)] = "primary" if singles == 0 else "secondary"
            singles += 1
    return kinds


def logical_dpi():
    """The screen's logical dots per inch: 72 on macOS, 96 on Windows and most
    Linux desktops, 96 with no application yet. A size the theme gives in
    pixels becomes points through this, so a 52 px reading is 52 px on every
    OS (at 72 dpi a point IS a pixel)."""
    try:
        screen = QApplication.primaryScreen() if QApplication.instance() else None
        return float(screen.logicalDotsPerInch()) if screen else 96.0
    except Exception:
        return 96.0


def px_to_pt(pixels):
    return pixels * 72.0 / logical_dpi()


def reading_pt(kind):
    """A reading's size in points: `theme.READING_SIZES` (px at the base
    font), following the launch font up to `READING_GROWTH`, and never
    smaller than the text beside it."""
    growth = min(max(theme.FONT_SIZE, 8) / 12.0, READING_GROWTH)
    return max(theme.size(STEP_READOUT),
               int(round(px_to_pt(theme.READING_SIZES[kind] * growth))))


def caption_pt():
    """A caption: `theme.CAPTION_SIZE` px, following the launch font, and
    never smaller than the text scale's caption step."""
    growth = max(theme.FONT_SIZE, 8) / 12.0
    wanted = min(px_to_pt(theme.CAPTION_SIZE * growth), theme.size(STEP_BASE) - 1)
    return max(theme.size(STEP_CAPTION), int(round(wanted)))


def tier_of(section):
    return int((section or {}).get("tier", 1) or 1)


def disclosure_text(sections, tier):
    """The words on a tier's disclosure: the first such section's own
    `disclosure`, else the theme's default."""
    for section in sections or ():
        if tier_of(section) == tier and section.get("disclosure"):
            return str(section["disclosure"])
    return theme.TIER_LABELS.get(tier, "Details")


def entry_rows(names, opened, columns):
    """The grid's rows: models in rows of at most `columns`, the earlier rows
    the fuller ("3, 2" for five, "2, 2" for four at three columns). Order is
    the Controller's.

    `opened=None` (or a name not in `names`) is the overview's grid: no
    leading row (K4). A name puts that model alone and full width first, then
    the rest - the pre-K4 sheet, kept because the rule is the same grid."""
    names = list(names)
    if not names:
        return []
    if opened not in names:
        opened = None
    rest = [n for n in names if n != opened]
    rows = [[opened]] if opened is not None else []
    columns = max(1, min(int(columns), MAX_SHEET_COLUMNS))
    if rest:
        count = -(-len(rest) // columns)
        base, extra = divmod(len(rest), count)
        start = 0
        for index in range(count):
            size = base + (1 if index < extra else 0)
            rows.append(rest[start:start + size])
            start += size
    return rows


#: Element types that are commands a row runs, rather than values it holds.
ACTION_TYPES = frozenset({"button", "file_save", "file_open"})


def is_action_row(section):
    """A `layout="row"` section that runs something is an action line, not a
    line of the table: Setup's Devices row (Refresh, the scan status) and its
    Launch row (the summary, Launch / Relaunch / Stop system). Given their own
    columns, they invented a "Scan" and a "Selected" column that every model
    row left empty, and pushed the table sideways with a scan message."""
    return (section.get("layout") == "row"
            and any(e.get("type") in ACTION_TYPES
                    for e in section.get("elements") or ()))


def numeral_family():
    """The numerals' face: a static "Archivo SemiExpanded" (Tk/Qt have no
    width axis, `design-Sheet.md`). When it is not installed Qt substitutes
    the text face (`install_font_fallbacks`), set at weight 600."""
    return f"{theme.NUMERAL_FAMILY} SemiExpanded"


def content_px(outer, padding, border):
    """The QSS `min-height` that makes a control `outer` px tall: in a Qt
    style sheet it is the content's height, inside the padding and edge."""
    return outer - 2 * (padding + border)


def stylesheet():
    """The whole Qt stylesheet, generated from `views.theme`.

    Every colour and every font size in this string comes from a theme value.
    That is the entire reason `style.qss` is deleted rather than ported: a
    checked-in sheet is a second palette that drifts from the other two views
    and cannot follow `--font-size` at launch.

    Two scales: the text scale (`theme.size`, ratio 1.2, one family) and the
    numeral scale for readings (`theme.READING_SIZES`, the numeral face at
    600). Every control has its hover, focus, pressed and disabled state
    here; focus is two pixels of ink everywhere (F25). Signal red is not in
    this sheet at all: the stop disc, the per-model switch and the
    unconfirmed mark dress themselves.
    """
    family, base_size = theme.FONT_FAMILY, theme.FONT_SIZE
    numerals = numeral_family()
    small_size = caption_pt()
    name_size = theme.size(STEP_READOUT)
    title_size = theme.size(STEP_TITLE)
    go_bg, go_fg = theme.colors("go")
    disabled_bg, disabled_fg = theme.DISABLED
    ink, muted, sheet_bg, panel = theme.TEXT, theme.MUTED, theme.BACKGROUND, theme.SURFACE
    control, input_radius = theme.RADIUS["control"], theme.RADIUS["input"]
    well_radius = theme.RADIUS["well"]
    hair, tight = theme.SPACE[0], theme.SPACE[1]
    ring = FOCUS_RING
    underline = f"2px solid {theme.INPUT_BORDER}"
    # A tick box's square is one line of the base font: points to pixels at
    # Qt's 96 dpi reference, since a sub-control's width takes no `pt`.
    indicator_px = int(round(base_size * 96 / 72))
    # The slider (the brief): a 4 px panel groove, the ink fill to the value,
    # an 18 px ink handle with a 2 px sheet border.
    groove, handle = theme.SPACE[1], theme.SPACE[6] - theme.SPACE[0]
    reading = {"font-family": numerals, "font-weight": "600"}
    # L4: a QSS min-height is the content's, so the edge and the padding are
    # taken off: every pressable is 24 px, a command 36 px (not 44 - the
    # owner's call is pending).
    command = f"{content_px(COMMAND_PX, theme.GAP, 1)}px"

    sheet = [
        _rule("QWidget", {"background-color": sheet_bg, "color": ink,
                          "font-family": family,
                          "font-size": f"{base_size}pt"}),
        _rule("QMainWindow, QDialog, QMessageBox",
              {"background-color": sheet_bg}),
        _rule("QMainWindow::separator", {"background": theme.RULE, "width": "1px",
                                         "height": "1px"}),
        # A label paints no rectangle of its own, so text sits on whatever it
        # is on: the sheet, the rail, a well.
        _rule("QLabel", {"background": "transparent"}),
        # Holders that only place other widgets paint nothing.
        _rule("QWidget#bare, QWidget#tableBar, QWidget#flow, QFrame#section, "
              "QFrame#entry, QWidget#sheet, QWidget#railList",
              {"background": "transparent"}),
        _rule("QLabel#caption, QLabel#unit, QLabel#axisLetter",
              {"color": muted, "font-size": f"{small_size}pt"}),
        # The table's furniture (Setup): a column caption is quiet, a row's
        # own name is not.
        _rule("QLabel#columnHeader", {"color": muted,
                                      "font-size": f"{small_size}pt",
                                      "padding-bottom": f"{hair}px"}),
        _rule("QLabel#rowTitle", {"color": ink, "font-weight": "600",
                                  "padding-right": f"{theme.INSET}px"}),
        # A reading is a number in the numeral face: ink at rest, trace only
        # while it changes (`live`), muted when frozen or stale.
        _rule("QLabel#reading", {"color": ink, **reading}),
        _rule('QLabel#reading[live="true"]', {"color": theme.TRACE}),
        _rule('QLabel#reading[quiet="true"]', {"color": muted}),
        _rule('QLabel#reading[scale="focal"]', {"font-size": f"{reading_pt('focal')}pt"}),
        _rule('QLabel#reading[scale="compact"]', {"font-size": f"{reading_pt('compact')}pt"}),
        _rule('QLabel#reading[scale="primary"]', {"font-size": f"{reading_pt('primary')}pt"}),
        _rule('QLabel#reading[scale="secondary"]', {"font-size": f"{reading_pt('secondary')}pt"}),
        # A readout (a statistic, a status word) is the numeral face at the
        # text scale, on the surface - never a box, so it never reads as an
        # entry.
        _rule("QLabel#valueLabel", {"color": ink, "font-weight": "600",
                                    "font-family": numerals,
                                    "padding": f"{hair}px 0px"}),
        _rule('QLabel#valueLabel[live="true"]', {"color": theme.TRACE}),
        _rule('QLabel#valueLabel[quiet="true"]', {"color": muted,
                                                  "font-weight": "400"}),
        # `_sync_dim` sets this on the panel - stale, a device lost, or the
        # model latched - and every number is muted at once, so a frozen
        # value never reads as a live one.
        _rule('QWidget[stale="true"] QLabel#valueLabel, '
              'QWidget[stale="true"] QLabel#reading', {"color": muted}),
        # A refusal sits in the section of the control that caused it (F10).
        _rule("QLabel#refusal", {"color": ink, "padding": f"{tight}px 0px"}),
        _rule("QLabel#staleLabel, QLabel#notice",
              {"color": ink, "padding": f"{hair}px 0px"}),
        # Inputs: a panel-toned well with a muted underline, no box; on a
        # well the fill steps back to the sheet (`design-Sheet.md`).
        _rule("QLineEdit, QComboBox",
              {"background-color": theme.WELL, "color": ink,
               "border": "none", "border-bottom": underline,
               "border-top-left-radius": f"{input_radius}px",
               "border-top-right-radius": f"{input_radius}px",
               "padding": f"{theme.GAP}px {theme.PAD}px",
               "selection-background-color": ink,
               "selection-color": sheet_bg}),
        _rule("QFrame#well QLineEdit, QFrame#well QComboBox",
              {"background-color": sheet_bg}),
        _rule("QLineEdit:hover, QComboBox:hover", {"border-bottom-color": ink}),
        _rule("QLineEdit:focus, QComboBox:focus, QTextEdit:focus",
              {"border": ring}),
        _rule("QLineEdit:disabled, QComboBox:disabled",
              {"background-color": disabled_bg, "color": disabled_fg,
               "border": f"1px dashed {muted}"}),
        _rule("QComboBox::drop-down", {"border": "none", "background": "transparent",
                                       "width": f"{theme.SPACE[6]}px"}),
        _rule("QComboBox QAbstractItemView",
              {"background-color": sheet_bg, "color": ink,
               "border": f"1px solid {muted}",
               "selection-background-color": panel,
               "selection-color": ink}),
        _rule("QTextEdit", {"background-color": theme.WELL, "color": ink,
                            "border": "2px solid transparent",
                            "border-radius": f"{input_radius}px",
                            "padding": f"{theme.GAP}px"}),
        # Buttons: `go` is ink-filled; every other role is outlined in ink on
        # the sheet; disabled is the sheet, 45 % ink and a dashed muted edge.
        _rule("QPushButton", {"background-color": sheet_bg, "color": ink,
                              "border": f"1px solid {ink}",
                              "border-radius": f"{control}px",
                              "padding": f"{theme.GAP}px {theme.INSET}px",
                              "min-height": command,
                              "font-weight": "500"}),
    ]
    for role in theme.ROLES:
        # One red. A button that merely *says* stop (Red Percent's "Stop"
        # run) is an ordinary command; signal red is spent on the stop disc,
        # which paints itself.
        background, foreground = (go_bg, go_fg) if role == "go" else (sheet_bg, ink)
        sheet.append(_rule(f'QPushButton[role="{role}"]',
                           {"background-color": background,
                            "color": foreground}))
    # States after the roles, so they win at equal specificity.
    sheet += [
        _rule("QPushButton:hover", {"background-color": theme.LIFT}),
        _rule('QPushButton[role="go"]:hover', {"background-color": go_bg,
                                               "border-color": muted}),
        _rule("QPushButton:focus", {"border": ring}),
        _rule("QPushButton:pressed", {"background-color": panel}),
        _rule("QPushButton:disabled", {"background-color": disabled_bg,
                                       "color": disabled_fg,
                                       "border": f"1px dashed {muted}"}),
        # G3's tick box: a square outlined in ink; ticked, an ink check is
        # painted on it (`TickBox`). Focus is the same ink ring, round the
        # whole control.
        _rule("QCheckBox", {"background": "transparent",
                            "spacing": f"{theme.GAP}px",
                            "border": "2px solid transparent",
                            "border-radius": f"{input_radius}px",
                            "padding": f"{hair}px",
                            "min-height": f"{content_px(TARGET_PX, hair, 2)}px"}),
        _rule("QCheckBox:focus", {"border": ring}),
        _rule("QCheckBox::indicator",
              {"width": f"{indicator_px}px", "height": f"{indicator_px}px",
               "background-color": sheet_bg,
               "border": f"1px solid {ink}",
               "border-radius": f"{input_radius}px"}),
        _rule("QCheckBox::indicator:hover", {"background-color": theme.LIFT}),
        _rule("QCheckBox::indicator:checked", {"background-color": sheet_bg}),
        _rule("QCheckBox:disabled", {"color": disabled_fg}),
        _rule("QCheckBox::indicator:disabled",
              {"background-color": disabled_bg,
               "border": f"1px dashed {muted}"}),
        # The slider beside a speed entry.
        _rule("QSlider", {"background": "transparent", "border": "2px solid transparent",
                          "min-height": f"{content_px(TARGET_PX, 0, 2)}px"}),
        _rule("QSlider:focus", {"border": ring, "border-radius": f"{control}px"}),
        _rule("QSlider::groove:horizontal",
              {"height": f"{groove}px", "background": panel,
               "border-radius": f"{hair}px"}),
        _rule("QFrame#well QSlider::groove:horizontal", {"background": sheet_bg}),
        _rule("QSlider::sub-page:horizontal",
              {"background": ink, "border-radius": f"{hair}px"}),
        _rule("QSlider::handle:horizontal",
              {"background": ink, "border": f"2px solid {sheet_bg}",
               "width": f"{handle - 2 * hair}px",
               "margin": f"-{(handle - groove) // 2}px 0px",
               "border-radius": f"{handle // 2}px"}),
        _rule("QSlider::sub-page:horizontal:disabled", {"background": muted}),
        _rule("QSlider::handle:horizontal:disabled", {"background": muted}),
        # Disclosures: the tier's words beside an arrow, ink, no box.
        _rule("QToolButton#disclosure",
              {"background": "transparent", "color": ink, "border": "2px solid transparent",
               "border-radius": f"{control}px", "font-weight": "600",
               "padding": f"{hair}px {tight}px",
               "min-height": f"{content_px(TARGET_PX, hair, 2)}px"}),
        _rule("QToolButton#disclosure:hover", {"background": theme.LIFT}),
        _rule("QToolButton#disclosure:focus", {"border": ring}),
        # The tier-2 well and the tier-3 strip inside it.
        _rule("QFrame#well", {"background-color": panel,
                              "border-radius": f"{well_radius}px"}),
        _rule("QFrame#well QWidget#bare, QFrame#well QWidget#flow, "
              "QFrame#well QFrame#section", {"background": "transparent"}),
        _rule("QFrame#diagnostics", {"background": "transparent",
                                     "border": "none",
                                     "border-left": f"2px solid {muted}"}),
        # Chrome, not instrument controls: Setup and Quit, the tray's toggle,
        # an alert's acknowledgement.
        _rule("QPushButton#ghost, QToolButton#ghost",
              {"background": "transparent", "color": ink,
               "border": f"1px solid {ink}", "border-radius": f"{control}px",
               "padding": f"{theme.GAP}px {theme.INSET}px",
               "min-height": command, "font-weight": "500"}),
        _rule("QPushButton#ghost:hover, QToolButton#ghost:hover",
              {"background": theme.LIFT}),
        _rule("QPushButton#ghost:checked", {"background": panel}),
        # The rail's Setup while Setup is shown (L19): ink-filled, as Tk's.
        _rule("QToolButton#ghost:checked", {"background": ink, "color": sheet_bg}),
        _rule("QPushButton#ghost:focus, QToolButton#ghost:focus",
              {"border": ring}),
        _rule("QPushButton#quiet", {"background": "transparent", "color": ink,
                                    "border": "2px solid transparent",
                                    "border-radius": f"{control}px",
                                    "padding": f"{theme.GAP}px {theme.PAD}px",
                                    "min-height": f"{content_px(COMMAND_PX, theme.GAP, 2)}px"}),
        _rule("QPushButton#quiet:hover", {"background": theme.LIFT}),
        _rule("QPushButton#quiet:focus", {"border": ring}),
        _rule("QPushButton#iconButton, QToolButton#iconButton",
              {"background": "transparent", "border": "2px solid transparent",
               "border-radius": f"{control}px", "padding": f"{hair}px",
               "min-height": f"{content_px(TARGET_PX, hair, 2)}px"}),
        _rule("QPushButton#iconButton:hover, QToolButton#iconButton:hover",
              {"background": theme.LIFT}),
        _rule("QPushButton#iconButton:focus, QToolButton#iconButton:focus",
              {"border": ring}),
        # The rail: the panel tone, full height, left.
        _rule("QFrame#rail", {"background-color": panel}),
        _rule("QFrame#rail QWidget, QFrame#rail QScrollArea",
              {"background": "transparent"}),
        _rule("QFrame#rail QLabel#railTitle", {"font-size": f"{name_size}pt",
                                               "font-weight": "600"}),
        _rule("QFrame#rail QLabel#railLatched", {"font-weight": "600"}),
        # The model list: the opened one highlighted on the sheet tone.
        _rule("QPushButton#railModel",
              {"background": "transparent", "color": ink, "border": "2px solid transparent",
               "border-radius": f"{control}px", "text-align": "left",
               "padding": f"{theme.GAP}px {theme.INSET}px", "font-weight": "400",
               "min-height": f"{content_px(TARGET_PX, theme.GAP, 2)}px"}),
        _rule("QPushButton#railModel:hover", {"background": theme.LIFT}),
        # The shown page: the sheet tone and a 2 px ink rule at its left (the
        # entry rule's language; QT7-18), not a 1.1:1 tone step alone.
        _rule("QPushButton#railModel:checked", {"background": sheet_bg,
                                                "border-left": f"2px solid {ink}",
                                                "font-weight": "600"}),
        _rule("QPushButton#railModel:focus", {"border": ring}),
        # The sheet: an entry is a 2 px ink rule and its name, nothing else.
        _rule("QFrame#entryRule", {"background-color": theme.RULE_STRONG,
                                   "border": "none"}),
        # A model's name: the base size on a closed entry, one step up on the
        # opened one (the artboard's 17 / 20 px).
        _rule("QLabel#entryName", {"font-size": f"{base_size}pt",
                                   "font-weight": "600"}),
        _rule('QLabel#entryName[opened="true"]', {"font-size": f"{name_size}pt"}),
        # K4: an overview entry's head is one press target (name, "Open" and
        # its chevron): a transparent 2 px edge that becomes the ink ring on
        # keyboard focus, the lift on hover. On the device page it is inert.
        _rule("QFrame#entryHead", {"background": "transparent",
                                   "border": "2px solid transparent",
                                   "border-radius": f"{control}px"}),
        _rule('QFrame#entryHead[pressable="true"]:hover', {"background": theme.LIFT}),
        _rule("QFrame#entryHead:focus", {"border": ring}),
        _rule("QToolButton#entryOpen", {"background": "transparent", "color": ink,
                                        "border": "none", "font-weight": "600",
                                        "padding": f"{hair}px {tight}px"}),
        _rule("QLabel#entryNote", {"color": ink, "font-size": f"{small_size}pt",
                                   "font-weight": "600"}),
        _rule("QLabel#headline", {"font-family": numerals, "font-weight": "600",
                                  "font-size": f"{reading_pt('primary')}pt"}),
        # What the headline asks of the operator (L1): ink, not a caption.
        _rule("QLabel#headlineSub", {"color": ink, "font-weight": "600",
                                     "font-size": f"{base_size}pt"}),
        _rule("QLabel#emptyTitle", {"font-size": f"{title_size}pt",
                                    "font-weight": "600"}),
        # Setup's dock: its title is the page's heading.
        _rule("QDockWidget", {"color": ink, "font-weight": "600",
                              "font-size": f"{title_size}pt"}),
        _rule("QDockWidget::title", {"background": sheet_bg, "color": ink,
                                     "padding": f"{theme.PAD}px {theme.INSET}px"}),
        _rule("QDockWidget::close-button, QDockWidget::float-button",
              {"background": "transparent", "border": "1px solid transparent",
               "border-radius": f"{control}px", "padding": f"{theme.GAP}px"}),
        _rule("QDockWidget::close-button:hover, QDockWidget::float-button:hover",
              {"background": theme.LIFT, "border-color": muted}),
        _rule("QFrame#table", {"background": "transparent"}),
        # An empty figure pane is its one caption line (L15).
        _rule("QLabel#figure", {"color": muted, "font-size": f"{small_size}pt"}),
        _rule("QScrollArea#wellScroll", {"background": "transparent", "border": "none"}),
        _rule("QLabel#dockTitle", {"font-weight": "600", "font-size": f"{title_size}pt"}),
        # The alert band and the tray, under the sheet: warnings and errors.
        _rule("QFrame#alertBand", {"background-color": sheet_bg,
                                   "border-top": f"1px solid {theme.RULE}"}),
        _rule("QFrame#alertBand QLabel#alertWord", {"font-weight": "600"}),
        _rule("QFrame#tray", {"background-color": sheet_bg,
                              "border-top": f"1px solid {theme.RULE}"}),
        _rule("QFrame#tray QTextEdit:focus", {"border": ring}),
        _rule('QLabel#trayLatest[severity="error"]', {"color": theme.SEVERITY_INK["error"]}),
        _rule('QLabel#trayLatest[severity="warning"]', {"color": theme.SEVERITY_INK["warning"]}),
        _rule('QLabel#trayLatest[severity="info"]', {"color": theme.SEVERITY_INK["info"]}),
        # The sheet scrolls as one page; it shows when it has focus (AUD-10).
        _rule("QScrollArea#sheetScroll, QScrollArea#setupScroll",
              {"border": "2px solid transparent", "background": sheet_bg}),
        _rule("QScrollArea#sheetScroll:focus, QScrollArea#setupScroll:focus",
              {"border": ring}),
        _rule("QScrollBar:vertical, QScrollBar:horizontal",
              {"background": "transparent", "border": "none",
               "width": f"{theme.SPACE[3]}px", "height": f"{theme.SPACE[3]}px"}),
        _rule("QScrollBar::handle", {"background": muted,
                                     "border-radius": f"{theme.GAP}px",
                                     "min-height": f"{theme.SPACE[6]}px",
                                     "min-width": f"{theme.SPACE[6]}px"}),
        _rule("QScrollBar::handle:hover", {"background": ink}),
        _rule("QScrollBar::add-line, QScrollBar::sub-line, "
              "QScrollBar::add-page, QScrollBar::sub-page",
              {"width": "0px", "height": "0px", "background": "transparent"}),
        _rule("QToolTip", {"background-color": sheet_bg, "color": ink,
                           "border": f"1px solid {muted}",
                           "padding": f"{theme.GAP}px"}),
    ]
    return "".join(sheet)


def allow_tab_to_every_control():
    """L7 (QT7-4): Tab reaches every control - buttons, the disc, the rail -
    on every OS. macOS's default reaches text fields and lists only unless
    Full Keyboard Access is on, so without this no button was ever focused
    there; Tk and Web reach them on the same Mac. Asked for everywhere, not
    branched on the OS: on Windows and Linux it is already the default."""
    hints = QGuiApplication.styleHints() if QGuiApplication.instance() else None
    if hints is not None and hints.tabFocusBehavior() != Qt.TabFocusBehavior.TabFocusAllControls:
        hints.setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)


def install_font_fallbacks():
    """The brief's faces when installed, the fallbacks when not: Public Sans
    -> Helvetica, and the numerals' static Archivo -> the text face (then
    set at 600, i.e. the text face bold). Qt consults these substitutions
    wherever the sheet names a family it cannot find."""
    for missing, fallbacks in (
            (theme.FONT_FAMILY, [theme.FONT_FALLBACK]),
            (numeral_family(), [theme.NUMERAL_FAMILY, theme.FONT_FAMILY,
                                theme.FONT_FALLBACK])):
        try:
            if missing not in QFontDatabase.families():
                QFont.insertSubstitutions(missing, fallbacks)
        except Exception:            # no QApplication yet: nothing to do
            pass



def to_physical_pixels(left, top, width, height, ratio,
                       screen_origin=(0, 0), screen_physical_origin=None):
    """Logical (Qt) rectangle -> physical pixels, for one screen.

    Qt reports mouse positions in *logical* pixels; the screen grabber wants
    *physical* ones. On a 200 % display those differ by a factor of two, so
    the captured region was offset and half-size — REDPERCENT-18's unverified
    half, and the reason this is a pure function with its own test rather than
    three lines buried in `mouseReleaseEvent`.

    `ratio` is the `devicePixelRatio` of the screen the drag happened on (not
    the primary screen's: on a mixed-DPI desk they differ). Offsets are
    measured from that screen's own logical origin, scaled, and then placed at
    the screen's physical origin, which defaults to `origin * ratio` — correct
    when every screen shares one scale factor, and overridable when they do
    not.
    """
    ratio = float(ratio or 1.0)
    origin_x, origin_y = screen_origin
    if screen_physical_origin is None:
        screen_physical_origin = (origin_x * ratio, origin_y * ratio)
    physical_x, physical_y = screen_physical_origin
    return (int(round(physical_x + (left - origin_x) * ratio)),
            int(round(physical_y + (top - origin_y) * ratio)),
            int(round(width * ratio)),
            int(round(height * ratio)))


def series_points(data):
    """Normalise whatever a `data_command` returned into [(x, y), ...].

    Accepts `{"x": [...], "y": [...]}`, `{"y": [...]}`, a flat list of
    numbers, or a list of pairs. Anything unreadable is dropped rather than
    raised: this runs on the render tick, and a malformed sample must not stop
    the panel from refreshing.
    """
    if not data:
        return []
    if isinstance(data, dict):
        ys = list(data.get("y") or [])
        xs = list(data.get("x") or range(len(ys)))
    elif isinstance(data, (list, tuple)):
        items = list(data)
        if items and isinstance(items[0], (list, tuple)) and len(items[0]) >= 2:
            xs = [item[0] for item in items]
            ys = [item[1] for item in items]
        else:
            ys, xs = items, list(range(len(items)))
    else:
        return []

    points = []
    for x, y in zip(xs, ys):
        try:
            points.append((float(x), float(y)))
        except (TypeError, ValueError):
            continue
    return points


def polyline_points(points, width, height, margin=5):
    """Scale a series to widget coordinates. Y grows downward, as Qt draws.

    A flat series (every y equal) is drawn down the middle rather than
    divided by a zero span.
    """
    if len(points) < 2 or width <= 0 or height <= 0:
        return []
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x_low, x_high = min(xs), max(xs)
    y_low, y_high = min(ys), max(ys)
    x_span = (x_high - x_low) or 1.0
    y_span = (y_high - y_low) or 1.0
    usable_width = max(width - 2 * margin, 1)
    usable_height = max(height - 2 * margin, 1)
    flat = y_high == y_low
    scaled = []
    for x, y in points:
        px = margin + (x - x_low) / x_span * usable_width
        py = (height / 2.0 if flat else
              height - margin - (y - y_low) / y_span * usable_height)
        scaled.append((px, py))
    return scaled


def validator_bounds(element):
    """(minimum, maximum, decimals) for a numeric entry, or None for text.

    Kept out of the widget so the PYSIDE-19 rule — the validator never narrows
    what `Param.parse` would accept — is checkable without a QApplication.
    """
    if element.get("value_type") not in ("int", "float"):
        return None
    low = element.get("min")
    high = element.get("max")
    decimals = 0 if element.get("value_type") == "int" else max(
        int(element.get("decimals") or 0), INPUT_DECIMALS)
    return (-1e12 if low is None else float(low),
            1e12 if high is None else float(high),
            decimals)


def int_bounds(element):
    """(minimum, maximum) for an `int` entry, or None for anything else.

    An integer parameter gets a `QIntValidator`, which refuses a decimal point
    outright — "5.5" cannot be typed into a field the model will only ever
    read as 5. `QDoubleValidator(..., decimals=0)` does not do that: it keeps
    accepting the point and simply calls the result intermediate, so the box
    looked as though it took the value.

    The bounds widen rather than narrow (floor the minimum, ceil the maximum),
    which is the PYSIDE-19 rule: the validator never refuses what `Param.parse`
    would accept. They are also clamped to a C int, which is what
    `QIntValidator` stores.
    """
    if element.get("value_type") != "int":
        return None
    low, high = element.get("min"), element.get("max")
    low = -INT_LIMIT if low is None else max(math.floor(low), -INT_LIMIT)
    high = INT_LIMIT if high is None else min(math.ceil(high), INT_LIMIT)
    return (int(low), int(high))


def display_text(element, text):
    """What a refresh is allowed to put into a widget.

    An `int` entry shows an integer. `Param.format` already renders one, but
    an element whose `model_attr` has no `Param` behind it — or one a model
    formats for itself — still arrives as "5.000", and a box guarded by a
    `QIntValidator` will not accept the text the refresh just wrote into it:
    the operator then edits a field that rejects its own contents. Anything
    unreadable is passed through untouched rather than blanked.
    """
    text = "" if text is None else str(text)
    if element.get("value_type") != "int" or not text.strip():
        return text
    try:
        return str(int(round(float(text))))
    except (TypeError, ValueError):
        return text


def dialog_filter(extensions):
    """A Qt file-dialog filter string for the schema's declared extensions."""
    exts = [e.lstrip(".") for e in (extensions or ("csv",))]
    parts = [f"{e.upper()} files (*.{e})" for e in exts]
    return ";;".join(parts + ["All files (*)"])


def with_extension(path, extensions):
    """Append the first declared extension when the operator typed none.

    `QFileDialog.getSaveFileName`'s filter is a display hint only — PYSIDE-18:
    on Linux a bare filename saved with no extension at all, where Tk's
    `defaultextension` had always supplied one.
    """
    if not path or os.path.splitext(path)[1]:
        return path
    exts = [e.lstrip(".") for e in (extensions or ("csv",))]
    return f"{path}.{exts[0]}" if exts else path



# ---------------------------------------------------------------------------
# Section containers: what `_make_section` hands to every element builder
#
# `schema.section(..., layout=)` is a hint the renderer has to honour, so the
# two layouts are containers with one API rather than an `if` inside each of
# the builders. None of them is a QWidget - they only place widgets someone
# else made - which is why the module still imports without PySide6.
# ---------------------------------------------------------------------------

def _caption(text):
    """A label over a control: sentence case, no colon, the muted ink."""
    label = QLabel(sentence_case(text))
    label.setObjectName("caption")
    return label


def _unit(text):
    label = QLabel(text)
    label.setObjectName("unit")
    return label


def _bare_row(*widgets, stretch=False, spacing=None):
    """Widgets side by side in a holder that paints nothing."""
    holder = QWidget()
    holder.setObjectName("bare")
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.GAP if spacing is None else spacing)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch(1)
    return holder


def _column(spacing=None):
    """A holder with a vertical layout that paints nothing."""
    holder = QWidget()
    holder.setObjectName("bare")
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.GAP if spacing is None else spacing)
    return holder, layout


class FlowLayout(QLayout):
    """Items left to right, wrapping like words, each line aligned on its
    foot so a button sits level with the entry beside it.

    This is what "never clip a number" (F5) means on the sheet: an item is
    never drawn narrower than its minimum; when a line is full the next item
    starts a new one. A widget whose `wide` property is set takes a line of
    its own at the full width (a plot, a log). Height follows width, so an
    entry grows by a line instead of cutting one off.
    """

    def __init__(self, parent=None, spacing=None, line_spacing=None):
        QLayout.__init__(self, parent)
        self._items = []
        self._spacing = theme.SPACE[6] if spacing is None else spacing
        self._line_spacing = theme.SPACE[4] if line_spacing is None else line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # -- QLayout's contract ---------------------------------------------------
    def addItem(self, item):                # noqa: N802 - Qt's name
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):                # noqa: N802 - Qt's name
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index):                # noqa: N802 - Qt's name
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):          # noqa: N802 - Qt's name
        return Qt.Orientation(0)

    def hasHeightForWidth(self):            # noqa: N802 - Qt's name
        return True

    def heightForWidth(self, width):        # noqa: N802 - Qt's name
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):            # noqa: N802 - Qt's name
        QLayout.setGeometry(self, rect)
        self._arrange(rect, apply=True)

    def sizeHint(self):                     # noqa: N802 - Qt's name
        """Everything on one line: what the section would like."""
        shown = [i for i in self._items if not i.isEmpty()]
        margins = self.contentsMargins()
        width = sum(i.sizeHint().width() for i in shown)
        width += self._spacing * max(0, len(shown) - 1)
        height = max((i.sizeHint().height() for i in shown), default=0)
        return QSize(width + margins.left() + margins.right(),
                     height + margins.top() + margins.bottom())

    def minimumSize(self):                  # noqa: N802 - Qt's name
        """The widest single item: below that something would be cut."""
        margins = self.contentsMargins()
        width = max((i.minimumSize().width() for i in self._items
                     if not i.isEmpty()), default=0)
        return QSize(width + margins.left() + margins.right(),
                     margins.top() + margins.bottom())

    # -- the layout itself ----------------------------------------------------
    def _arrange(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        room = max(area.width(), 1)
        lines, line, used = [], [], 0
        for item in self._items:
            if item.isEmpty():
                continue
            widget = item.widget()
            wide = bool(widget is not None and widget.property("wide"))
            hint, least = item.sizeHint(), item.minimumSize()
            width = room if wide else max(least.width(), min(hint.width(), room))
            height = (item.heightForWidth(width) if item.hasHeightForWidth()
                      else hint.height())
            height = max(height, least.height())
            if line and (wide or used + self._spacing + width > room):
                lines.append(line)
                line, used = [], 0
            line.append((item, width, height))
            used += width + (self._spacing if len(line) > 1 else 0)
            if wide:
                lines.append(line)
                line, used = [], 0
        if line:
            lines.append(line)
        top = area.y()
        for index, entries in enumerate(lines):
            tallest = max(h for _, _, h in entries)
            left = area.x()
            for item, width, height in entries:
                if apply:
                    item.setGeometry(QRect(left, top + tallest - height,
                                           width, height))
                left += width + self._spacing
            top += tallest + (self._line_spacing if index < len(lines) - 1 else 0)
        return top - rect.y() + margins.bottom()


class FlowSection:
    """A column section on the sheet: captioned controls in a `FlowLayout`.

    `add` puts the caption over the control and the unit after it; `add_inline`
    puts a caption and a value on one line (a status word, a lamp); `add_axis`
    collects a section's axis readings under ONE caption with the letters
    inline ("Position" once, then X Y Z); `add_wide` gives a plot or a log a
    line of its own. Every call returns the holder, so the panel can hide a
    normal value in tier 1 (status by exception).
    """

    #: A flow section lets a control size itself.
    control_width = 0
    is_row = False

    def __init__(self, flow):
        self.flow = flow
        self._axis = None

    def add(self, label, widget, unit=""):
        if not label and not unit:
            self.flow.addWidget(widget)
            return widget
        holder, column = _column()
        if label:
            column.addWidget(_caption(label))
        if unit:
            column.addWidget(_bare_row(widget, _unit(unit), stretch=True))
        else:
            column.addWidget(widget)
        self.flow.addWidget(holder)
        return holder

    def add_inline(self, label, *widgets, lead=None):
        """[lead] caption value on one line: a status word, a lamp."""
        parts = ([lead] if lead is not None else [])
        parts += ([_caption(label)] if label else []) + list(widgets)
        holder = _bare_row(*parts, spacing=theme.PAD)
        self.flow.addWidget(holder)
        return holder

    def add_wide(self, label, widget):
        holder, column = _column()
        if label:
            column.addWidget(_caption(label))
        column.addWidget(widget)
        holder.setProperty("wide", True)
        self.flow.addWidget(holder)
        return holder

    def add_axis(self, caption, letter, value, unit=""):
        """One axis reading, joining the section's axis group."""
        if self._axis is None:
            holder, column = _column()
            text = caption + (f", {unit}" if unit else "")
            column.addWidget(_caption(text))
            numbers = QWidget()
            numbers.setObjectName("flow")
            layout = FlowLayout(numbers, spacing=theme.SPACE[9],
                                line_spacing=theme.SPACE[1])
            column.addWidget(numbers)
            self.flow.addWidget(holder)
            self._axis = (holder, layout)
        holder, layout = self._axis
        mark_label = QLabel(letter)
        mark_label.setObjectName("axisLetter")
        pair = _bare_row(mark_label, value, spacing=theme.PAD)
        pair.layout().setAlignment(mark_label, Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(pair)
        return pair


class PanelTable:
    """The shared grid that every `layout="row"` section adds one line to.

    Columns are allocated **by label**, not by position, so "Port" sits under
    "Port" in every row even in a row that declares no gamepad and no port at
    all. That is what makes the Setup panel read as a table — one line per
    model, aligned — instead of as six ragged lines of different lengths.

    Column 0 is the row's own name. The header - one caption per column,
    once - sits directly above the first table row; an action line
    (`is_action_row`) spans the table instead of claiming columns, so Setup's
    Devices line sits above the header and its Launch line under the last row.
    """

    #: Where the header lands when the table opens on a table row.
    HEADER_ROW = 0

    def __init__(self, grid):
        self.grid = grid
        self.columns = {}        # label -> column index
        self.rows = -1           # the last grid row used
        self.header_row = None   # claimed by the first table row
        self._next_column = 1    # column 0 belongs to the row titles

    def add_row(self, title):
        """One line. An **untitled** section claims no name column at all."""
        if self.header_row is None:
            self.rows += 1
            self.header_row = self.rows
        self.rows += 1
        label = None
        if title:
            label = RowTitle(title)
            self.grid.addWidget(label, self.rows, 0)
        return TableRow(self, self.rows, label)

    def add_bar(self, title):
        """An action line across the whole table: its name, its readouts, and
        its commands pushed to the right edge."""
        self.rows += 1
        bar = TableBar(title)
        self.grid.addWidget(bar.widget, self.rows, 0, 1, -1)
        return bar

    def column_for(self, label, narrow=False):
        """This label's column, allocating one the first time it is seen. An
        *unlabelled* widget gets a fresh column of its own; a `narrow` column
        (a tick box: Setup's Launch column, G3) is as wide as its header or
        its box and never takes the control floor."""
        key = (label or "").strip()
        if not key:
            return self._claim(None, narrow)
        if key not in self.columns:
            self.columns[key] = self._claim(key, narrow)
        return self.columns[key]

    def _claim(self, header, narrow=False):
        column = self._next_column
        self._next_column += 1
        if not narrow:
            self.grid.setColumnMinimumWidth(column, TABLE_CONTROL_MIN_PX)
        self.grid.setColumnStretch(column, 0)
        if header:
            caption = QLabel(sentence_case(header))
            caption.setObjectName("columnHeader")
            self.grid.addWidget(caption, self.header_row or 0, column)
        return column


class TableRow:
    """`layout="row"`: one model's line in the panel's table."""

    control_width = TABLE_CONTROL_MIN_PX
    is_row = True

    def __init__(self, table, row, title=None):
        self.table, self.row, self.title = table, row, title

    def add(self, label, widget, unit=""):
        # A tick box is a narrow column of its own, its header its caption;
        # so is a readout (a status word needs no control's floor, L8).
        narrow = isinstance(widget, (QCheckBox, QLabel))
        if (isinstance(widget, QCheckBox) and self.title is not None
                and self.title.target is None):
            self.title.set_target(widget)       # L4: one target, name and tick
        self.table.grid.addWidget(widget, self.row,
                                  self.table.column_for(label, narrow))
        return widget

    def add_inline(self, label, *widgets, lead=None):
        widgets = ((lead,) if lead is not None else ()) + widgets
        return self.add(label, _bare_row(*widgets) if len(widgets) > 1 else widgets[0])

    def add_axis(self, caption, letter, value, unit=""):
        return self.add(letter, value)

    #: A row has one line; "wide" has nothing to mean here.
    def add_wide(self, label, widget):
        return self.add(label, widget)


class RowTitle(QLabel):
    """A table row's own name. In a row with a tick box, the name is part of
    the tick's target (L4): a press on "Stepper Probe" ticks its box."""

    def __init__(self, text, parent=None):
        QLabel.__init__(self, text, parent)
        self.setObjectName("rowTitle")
        self.target = None

    def set_target(self, box):
        self.target = box
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(TARGET_PX)

    def mouseReleaseEvent(self, event):     # noqa: N802 - Qt's name
        box = self.target
        if (box is not None and box.isEnabled()
                and event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            event.accept()
            box.click()
            return
        super().mouseReleaseEvent(event)


class TableBar:
    """An action line: `title  caption value ...        [command] [command]`.

    A readout's value wraps rather than clips, so a scan message as long as a
    macOS device path gets the room it needs without pushing a column over.
    """

    control_width = 0
    is_row = True

    def __init__(self, title):
        self.widget = QWidget()
        self.widget.setObjectName("tableBar")
        self.layout = QHBoxLayout(self.widget)
        self.layout.setContentsMargins(0, theme.GAP, 0, theme.GAP)
        self.layout.setSpacing(theme.PAD)
        if title:
            name = QLabel(title)
            name.setObjectName("rowTitle")
            self.layout.addWidget(name)
        self._values = QHBoxLayout()
        self._values.setSpacing(theme.PAD)
        self.layout.addLayout(self._values, 1)
        self._commands = QHBoxLayout()
        self._commands.setSpacing(theme.PAD)
        self.layout.addLayout(self._commands)

    def add(self, label, widget, unit=""):
        if isinstance(widget, QAbstractButton):
            self._commands.addWidget(widget)
            return widget
        if label:
            self._values.addWidget(_caption(label))
        if isinstance(widget, QLabel):
            widget.setWordWrap(True)
        self._values.addWidget(widget, 1)
        return widget

    def add_inline(self, label, *widgets, lead=None):
        widgets = ((lead,) if lead is not None else ()) + widgets
        for widget in widgets:
            self.add(label, widget)
            label = ""
        return widgets[-1] if widgets else None

    def add_axis(self, caption, letter, value, unit=""):
        return self.add(letter, value)

    def add_wide(self, label, widget):
        return self.add(label, widget)


# ---------------------------------------------------------------------------
# Pure widgets
# ---------------------------------------------------------------------------

def text_px(chars, bold=False):
    """`chars` average characters of the base text face, in pixels."""
    font = QFont(theme.FONT_FAMILY, theme.FONT_SIZE)
    font.setBold(bold)
    return QFontMetrics(font).horizontalAdvance("0" * chars)


class SeriesPlot(QWidget):
    """The `plot` element's inline drawing surface.

    A `QPainter` polyline, not an embedded matplotlib canvas: this is a live
    readout that ticks at the render rate, and Tk draws the same thing on a
    `tk.Canvas`. Drawn on whatever it sits on (a well), a muted x-axis only,
    the line in the trace - or muted once the model is latched and the line
    has stopped being live (`design-Sheet.md`).

    Empty, it is one caption line: the schema's `empty` sentence (L15), not
    a tall box of nothing.
    """

    MARGIN_PX = 5
    HEIGHT_PX = 140

    def __init__(self, role="neutral", parent=None, empty=""):
        QWidget.__init__(self, parent)
        self.role = role
        self.frozen = False
        self.empty_text = str(empty or "No data yet.")
        self._points = []
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self._sync_height()

    @property
    def points(self):
        return list(self._points)

    @property
    def is_empty(self):
        return len(self._points) < 2

    def _empty_height(self):
        return caption_line_px() + 2 * theme.SPACE[1]

    def _sync_height(self):
        height = self._empty_height() if self.is_empty else self.HEIGHT_PX
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
            self.updateGeometry()

    def sizeHint(self):                     # noqa: N802 - Qt's name
        return QSize(self.minimumWidth(), self.minimumHeight())

    def minimumSizeHint(self):              # noqa: N802 - Qt's name
        return QSize(self.minimumWidth(), self.minimumHeight())

    def set_series(self, data):
        points = series_points(data)
        if points != self._points:
            self._points = points
            self._sync_height()
            self.update()

    def set_frozen(self, frozen):
        if bool(frozen) != self.frozen:
            self.frozen = bool(frozen)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        muted = QColor(theme.MUTED)
        if self.is_empty:
            # The empty sentence, a caption at the left: what to do next.
            painter.setPen(muted)
            font = QFont(self.font())
            font.setPointSizeF(caption_pt())
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter, self.empty_text)
            return
        axis = QPen(muted)
        axis.setWidth(1)
        painter.setPen(axis)
        bottom = self.height() - 1
        painter.drawLine(0, bottom, self.width(), bottom)
        scaled = polyline_points(self._points, self.width(), self.height(),
                                 self.MARGIN_PX)
        pen = QPen(QColor(theme.MUTED if self.frozen else theme.TRACE))
        pen.setWidthF(1.75)
        painter.setPen(pen)
        path = QPainterPath(QPointF(*scaled[0]))
        for point in scaled[1:]:
            path.lineTo(QPointF(*point))
        painter.drawPath(path)


def caption_line_px():
    """One caption line's height, in pixels, at the launch font."""
    font = QFont(theme.FONT_FAMILY)
    font.setPointSizeF(caption_pt())
    return QFontMetrics(font).height()


def numeral_font(pixels, weight=None):
    """The numerals' face at a pixel size, weight 600 (the stop 700), with
    tabular figures where the face has them."""
    font = QFont()
    font.setFamilies([numeral_family(), theme.NUMERAL_FAMILY, theme.FONT_FAMILY,
                      theme.FONT_FALLBACK])
    font.setPixelSize(max(8, int(pixels)))
    font.setWeight(QFont.Weight(weight or theme.NUMERAL_WEIGHT))
    try:                                  # tabular figures, where Qt has them
        font.setFeature(QFont.Tag("tnum"), 1)
    except (AttributeError, TypeError):
        pass
    return font


class StopButton(QPushButton):
    """The stop object: A's disc (the brief) - a red face reading `Stop`, a
    sheet-coloured gap and a red ring. Always red; latched it reads `Clear`,
    the ring thickens from `theme.STOP["ring"]` to `["ring_latched"]` and
    swells exactly once, on the edge where the latch closes. It is never
    disabled and never dimmed - `setEnabled(False)` is refused here, because a
    gate on the one control that stops things is the defect, not a state.

    Keyboard: Space, Return and Enter all press it (AUD-4). Focus is an ink
    ring painted *outside* the disc, in a margin kept for it, so it shows in
    both states and is never mistaken for the latched ring.
    """

    #: The focus ring's width, and the margin kept around the disc for it.
    FOCUS_PX = 2
    FOCUS_GAP = 3

    def __init__(self, parent=None, diameter=None):
        QPushButton.__init__(self, "Stop", parent)
        self.is_latched = False
        self._ring_now = None
        self._hover = False
        self.setObjectName("stop")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Reached by Tab, not handed focus at boot: a focus ring on it before
        # anything has happened reads as the latch.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(PULSE_MS)
        self._pulse.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pulse.valueChanged.connect(self._on_pulse)
        self._pulse.finished.connect(self._on_pulse_done)
        self.set_diameter(diameter or theme.STOP["diameter"])

    @staticmethod
    def line_height():
        """One line of the theme's base font - not this widget's font, which
        the sheet has not reached yet when a control is sized."""
        return QFontMetrics(QFont(theme.FONT_FAMILY, theme.FONT_SIZE)).height()

    @property
    def diameter(self):
        return self._diameter

    def set_diameter(self, diameter):
        """The disc's diameter in px (`theme.STOP`); the widget adds the focus
        margin round it."""
        self._diameter = int(diameter)
        side = self._diameter + 2 * (self.FOCUS_GAP + self.FOCUS_PX)
        if self.width() != side or self.height() != side:
            self.setFixedSize(side, side)
        self.update()

    def ring_px(self):
        return theme.STOP["ring_latched" if self.is_latched else "ring"]

    def face_rect(self):
        inset = self.FOCUS_GAP + self.FOCUS_PX + self.ring_px() + theme.STOP["gap"]
        return QRectF(inset, inset, self.width() - 2 * inset,
                      self.height() - 2 * inset)

    def face_size(self):
        """The face's pixel size: a quarter of the disc, or smaller if "Clear"
        would not sit inside the face."""
        room = self.face_rect().width() * 0.8
        size = max(8, int(self._diameter * 0.25))
        while size > 8:
            if QFontMetrics(numeral_font(size, 700)).horizontalAdvance("Clear") <= room:
                break
            size -= 1
        return size

    def setEnabled(self, enabled):         # noqa: N802 - Qt's name
        """Never dimmed: the stop answers in every mode."""
        QPushButton.setEnabled(self, True)

    def set_latched(self, is_latched, face=None):
        """Face and ring from the state, never from the last click. Called on
        every tick; it repaints only when something it draws has changed.
        `face` is `stop_words`' word when the dashboard has it (L1)."""
        is_latched = bool(is_latched)
        was = self.is_latched
        self.is_latched = is_latched
        wanted = face or ("Clear" if is_latched else "Stop")
        if self.text() != wanted:
            self.setText(wanted)
        if is_latched and not was:
            self._pulse.stop()
            if not motion_reduced():
                ring = float(theme.STOP["ring_latched"])
                self._pulse.setStartValue(ring)
                self._pulse.setKeyValueAt(0.45, ring * 2.0)
                self._pulse.setEndValue(ring)
                self._pulse.start()       # once, on the edge
        elif not is_latched:
            self._pulse.stop()
            self._ring_now = None
        if was != is_latched:
            self.update()

    def _on_pulse(self, value):
        self._ring_now = float(value)
        self.update()

    def _on_pulse_done(self):
        self._ring_now = None
        self.update()

    def _dress(self, ring=None):
        """Repaint (the disc paints itself; there is no sheet to swap)."""
        if ring is not None:
            self._ring_now = float(ring)
        self.update()

    # -- Qt event handlers ------------------------------------------------
    def event(self, event):
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self._hover = event.type() == QEvent.Type.HoverEnter
            self.update()
        handled = QPushButton.event(self, event)
        if event.type() in (QEvent.Type.Polish, QEvent.Type.StyleChange):
            # The sheet's command floor (L4) is for commands; the disc is its
            # own size, whatever a style polish set.
            side = self._diameter + 2 * (self.FOCUS_GAP + self.FOCUS_PX)
            if self.minimumSize() != QSize(side, side) or self.maximumSize() != QSize(side, side):
                self.setFixedSize(side, side)
        return handled

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not event.isAutoRepeat():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        red = QColor(theme.SIGNAL)
        outer = self.FOCUS_GAP + self.FOCUS_PX
        ring = self._ring_now if self._ring_now is not None else self.ring_px()
        ring += 1 if self._hover and not self.isDown() else 0
        disc = QRectF(outer, outer, self.width() - 2 * outer,
                      self.height() - 2 * outer)
        # The ring and the gap: a red disc, then a sheet-coloured one inside it.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(red)
        painter.drawEllipse(disc)
        painter.setBrush(QColor(theme.BACKGROUND))
        painter.drawEllipse(disc.adjusted(ring, ring, -ring, -ring))
        face = self.face_rect()
        if self.isDown():
            face = face.adjusted(2, 2, -2, -2)
        painter.setBrush(red)
        painter.drawEllipse(face)
        painter.setPen(QColor(theme.colors(DANGER_ROLE)[1]))
        painter.setFont(numeral_font(self.face_size(), 700))
        painter.drawText(face, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus():
            pen = QPen(QColor(theme.STOP_FOCUS))
            pen.setWidthF(self.FOCUS_PX)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = self.FOCUS_PX / 2.0
            painter.drawEllipse(QRectF(inset, inset, self.width() - 2 * inset,
                                       self.height() - 2 * inset))
        painter.end()


class SwitchButton(QAbstractButton):
    """A model's own stop (tier 3): a small switch, not a second red disc.
    Off, a muted track and knob; latched, a red track and a white knob. Its
    words are the schema's ("Stop" / "Stopped"); its tooltip names the model.
    `is_on` comes from the state, never from the click."""

    def __init__(self, text="", parent=None):
        QAbstractButton.__init__(self, parent)
        self.setText(text)
        self.is_on = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("switch")

    def set_on(self, is_on):
        if bool(is_on) != self.is_on:
            self.is_on = bool(is_on)
            self.update()

    def _track(self):
        width, height = theme.SWITCH["track"]
        top = (self.height() - height) / 2.0
        return QRectF(3, top, width, height)

    def sizeHint(self):                     # noqa: N802 - Qt's name
        width, height = theme.SWITCH["track"]
        text = QFontMetrics(QFont(theme.FONT_FAMILY, theme.FONT_SIZE))
        return QSize(width + 6 + theme.PAD + text.horizontalAdvance(self.text()) + 2,
                     max(height + 6, text.height() + theme.GAP))

    minimumSizeHint = sizeHint

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track()
        radius = track.height() / 2.0
        knob = theme.SWITCH["knob"]
        enabled = self.isEnabled()
        if self.is_on:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.SWITCH["on_fill"] if enabled
                                    else theme.DISABLED[1]))
            painter.drawRoundedRect(track, radius, radius)
            knob_colour, left = theme.SWITCH["knob_on"], track.right() - radius
        else:
            edge = QPen(QColor(theme.SWITCH["off_edge"] if enabled else theme.DISABLED[1]))
            edge.setWidthF(1.5)
            painter.setPen(edge)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(0.75, 0.75, -0.75, -0.75),
                                    radius, radius)
            knob_colour = theme.SWITCH["knob_off"] if enabled else theme.DISABLED[1]
            left = track.left() + radius
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(knob_colour))
        painter.drawEllipse(QPointF(left, track.center().y()), knob / 2.0, knob / 2.0)
        if self.hasFocus():
            pen = QPen(QColor(theme.STOP_FOCUS))
            pen.setWidthF(2)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)
        painter.setPen(QColor(theme.TEXT if enabled else theme.DISABLED[1]))
        painter.setFont(self.font())
        text = QRectF(track.right() + theme.PAD, 0,
                      self.width() - track.right() - theme.PAD, self.height())
        painter.drawText(text, int(Qt.AlignmentFlag.AlignLeft
                                   | Qt.AlignmentFlag.AlignVCenter), self.text())
        painter.end()


class KeySlider(QSlider):
    """The speed slider (L6): an arrow is 1 % of the travel and a page key
    10 %; Home and End do nothing - one key must never commit the maximum
    speed."""

    def keyPressEvent(self, event):         # noqa: N802 - Qt's name
        if event.key() in (Qt.Key.Key_Home, Qt.Key.Key_End):
            event.accept()
            return
        super().keyPressEvent(event)


class TickBox(QCheckBox):
    """G3's tick box, H10: ticked, an ink check is drawn on the square - a
    tick, not a filled block that reads as a lamp."""

    def paintEvent(self, event):
        QCheckBox.paintEvent(self, event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        box = QRectF(self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, option, self))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(theme.TEXT if self.isEnabled() else theme.DISABLED[1]))
        pen.setWidthF(max(2.0, box.width() / 8.0))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath(QPointF(box.left() + box.width() * 0.24,
                                    box.top() + box.height() * 0.52))
        path.lineTo(box.left() + box.width() * 0.43, box.top() + box.height() * 0.71)
        path.lineTo(box.left() + box.width() * 0.77, box.top() + box.height() * 0.30)
        painter.drawPath(path)
        painter.end()


class ElidedLabel(QLabel):
    """One line that never clips a word: too long, it ends in an ellipsis and
    the whole text is in the tooltip.

    `min_chars` makes it a layout citizen: it asks for its whole text, and
    gives way down to that many characters and an ellipsis. Without it, it
    takes what it is given (the tray's line).
    """

    def __init__(self, parent=None, mode=None, min_chars=None):
        QLabel.__init__(self, "", parent)
        self._full = ""
        self._mode = mode or Qt.TextElideMode.ElideRight
        self._min_chars = min_chars
        if min_chars is None:
            self.setSizePolicy(QSizePolicy.Policy.Ignored,
                               QSizePolicy.Policy.Preferred)
        else:
            self.setSizePolicy(QSizePolicy.Policy.Preferred,
                               QSizePolicy.Policy.Preferred)

    def set_full_text(self, text):
        text = text or ""
        if text == self._full and self.text():
            return
        self._full = text
        self.updateGeometry()
        self._elide()

    def full_text(self):
        return self._full

    def _extra(self):
        margins = self.contentsMargins()
        return margins.left() + margins.right() + 2 * self.margin() + 2

    def sizeHint(self):
        hint = QLabel.sizeHint(self)
        if self._min_chars is None:
            return hint
        return QSize(self.fontMetrics().horizontalAdvance(self._full)
                     + self._extra(), hint.height())

    def minimumSizeHint(self):
        hint = QLabel.minimumSizeHint(self)
        if self._min_chars is None:
            return QSize(0, hint.height())
        head = self._full[:self._min_chars]
        if len(head) < len(self._full):
            head += "…"
        return QSize(self.fontMetrics().horizontalAdvance(head) + self._extra(),
                     hint.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        width = max(self.contentsRect().width(), 1)
        shown = self.fontMetrics().elidedText(self._full, self._mode, width)
        if shown != self.text():
            self.setText(shown)
        tip = self._full if shown != self._full else ""
        if self._min_chars is None:
            tip = self._full
        if self.toolTip() != tip:
            self.setToolTip(tip)


class NoteLabel(QLabel):
    """A line that wraps rather than clips (the rail's status line, which at
    200 px reads "Simulation, no hardware / attached")."""

    def __init__(self, text="", parent=None):
        QLabel.__init__(self, text, parent)
        self.setWordWrap(True)

    def set_full_text(self, text):
        text = text or ""
        if text != self.text():
            self.setText(text)
        self.setVisible(bool(text))

    def full_text(self):
        return self.text()


class ReadoutLabel(QLabel):
    """A panel readout: whole when it fits, elided from the middle with the
    whole value as its tooltip when it does not. It asks for no more than
    `READOUT_MAX_CHARS`, so a 60-character Run ID cannot overprint the next
    column or force the panel to scroll sideways (F15, HC). `text()` is
    always the whole value; only the painting is shortened."""

    def __init__(self, text="", parent=None):
        QLabel.__init__(self, text, parent)
        self._tip_is_ours = False

    def setText(self, text):                # noqa: N802 - Qt's name
        QLabel.setText(self, text)
        self.updateGeometry()
        self._sync_tip()

    def sizeHint(self):
        hint = QLabel.sizeHint(self)
        cap = self.fontMetrics().averageCharWidth() * READOUT_MAX_CHARS
        return QSize(min(hint.width(), cap), hint.height())

    def minimumSizeHint(self):
        hint = self.sizeHint()
        return QSize(min(hint.width(),
                         self.fontMetrics().averageCharWidth() * 8), hint.height())

    def is_elided(self):
        return (self.fontMetrics().horizontalAdvance(self.text())
                > self.contentsRect().width())

    def shown_text(self):
        """What is painted: the value, or its middle-elided form."""
        if not self.is_elided():
            return self.text()
        return self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideMiddle,
            max(self.contentsRect().width(), 1))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_tip()

    def _sync_tip(self):
        cap = self.fontMetrics().averageCharWidth() * READOUT_MAX_CHARS
        elided = (self.is_elided()
                  or self.fontMetrics().horizontalAdvance(self.text()) > cap)
        if elided and self.toolTip() != self.text():
            self.setToolTip(self.text())
            self._tip_is_ours = True
        elif not elided and self._tip_is_ours:
            self.setToolTip("")
            self._tip_is_ours = False

    def paintEvent(self, event):
        if not self.is_elided():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(self.contentsRect(),
                         int(Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignVCenter),
                         self.shown_text())
        painter.end()


class ReadingLabel(QLabel):
    """A reading: a number at `theme.READING_SIZES` in the numeral face. It is
    never elided and never squeezed - it asks for exactly its own width and
    the flow moves it to the next line instead (F5)."""

    def __init__(self, kind="primary", parent=None):
        QLabel.__init__(self, EMPTY_READOUT, parent)
        self.setObjectName("reading")
        self.setProperty("scale", kind)
        self.setProperty("live", "false")
        self.setProperty("quiet", "true")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

    def minimumSizeHint(self):              # noqa: N802 - Qt's name
        return self.sizeHint()


class FigureLabel(QLabel):
    """A model-rendered figure (the `image` element), scaled down to the width
    it is given and never wider than it was drawn: a 640 px matplotlib figure
    must not force the sheet to scroll sideways at 28 pt.

    With no figure it is one caption line saying so (L15)."""

    def __init__(self, parent=None, empty=""):
        QLabel.__init__(self, "", parent)
        self._figure = None
        self.empty_text = str(empty or "Nothing to show yet.")
        self.setObjectName("figure")
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.set_figure(None)

    @property
    def is_empty(self):
        return self._figure is None or self._figure.isNull()

    def set_figure(self, pixmap):
        self._figure = pixmap
        if self.is_empty:
            self.clear()
            self.setText(self.empty_text)
            self.setMinimumHeight(caption_line_px())
        else:
            self.setText("")
            self.setMinimumHeight(SeriesPlot.HEIGHT_PX)
        self.updateGeometry()
        self._fit()

    def _natural(self):
        ratio = self._figure.devicePixelRatio() or 1.0
        return self._figure.width() / ratio, self._figure.height() / ratio

    def hasHeightForWidth(self):            # noqa: N802 - Qt's name
        return not self.is_empty

    def heightForWidth(self, width):        # noqa: N802 - Qt's name
        if self.is_empty:
            return self.minimumHeight()
        natural_w, natural_h = self._natural()
        shown = min(width, natural_w) if natural_w else width
        return max(self.minimumHeight(), int(natural_h * shown / max(natural_w, 1)))

    def sizeHint(self):                     # noqa: N802 - Qt's name
        if self.is_empty:
            return QSize(0, self.minimumHeight())
        natural_w, natural_h = self._natural()
        return QSize(int(natural_w), int(natural_h))

    def minimumSizeHint(self):              # noqa: N802 - Qt's name
        return QSize(0, self.minimumHeight())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        if self.is_empty:
            return
        natural_w, _ = self._natural()
        width = int(min(max(self.width(), 1), natural_w))
        ratio = self._figure.devicePixelRatio() or 1.0
        scaled = self._figure.scaledToWidth(int(width * ratio),
                                            Qt.TransformationMode.SmoothTransformation)
        scaled.setDevicePixelRatio(ratio)
        self.setPixmap(scaled)


class MiddleCombo(QComboBox):
    """A dropdown that elides the *middle* of a long choice.

    "/dev/cu.usbmodem14401" and "/dev/cu.usbmodem14501" differ at the end;
    cut at the right they both read "/dev/cu.usbmo" (HC-6), and fifty
    gamepads read the same. Shown elided in the middle, whole in the tooltip
    and whole in the open list, which is as wide as its longest choice.
    """

    def __init__(self, parent=None):
        QComboBox.__init__(self, parent)
        self.view().setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.currentTextChanged.connect(self._sync_tip)

    def _sync_tip(self, text):
        if self.toolTip() != text:
            self.setToolTip(text)

    def shown_text(self):
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        field = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxEditField, self)
        return self.fontMetrics().elidedText(
            self.currentText(), Qt.TextElideMode.ElideMiddle,
            max(field.width(), 1))

    def showPopup(self):                    # noqa: N802 - Qt's name
        widest = max((self.fontMetrics().horizontalAdvance(self.itemText(i))
                      for i in range(self.count())), default=0)
        self.view().setMinimumWidth(widest + 4 * theme.PAD)
        super().showPopup()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        option.currentText = self.shown_text()
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, option)


def _glyph(size, draw, inks=None):
    """An icon drawn from geometry in the theme's inks: muted at rest, ink
    under the pointer, the disabled ink when greyed."""
    icon = QIcon()
    for mode, ink in (inks or ((QIcon.Mode.Normal, theme.MUTED),
                               (QIcon.Mode.Active, theme.TEXT),
                               (QIcon.Mode.Disabled, theme.DISABLED[1]))):
        pixmap = QPixmap(size * 2, size * 2)
        pixmap.setDevicePixelRatio(2.0)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(ink))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        draw(painter, size, ink)
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def reload_icon(size=14):
    """A rescan glyph: an open ring and its arrowhead."""
    def draw(painter, side, _ink):
        inset = 2.5
        painter.drawArc(QRectF(inset, inset, side - 2 * inset, side - 2 * inset),
                        90 * 16, 280 * 16)
        tip_x, tip_y = side / 2.0, inset
        painter.drawLine(QPointF(tip_x, tip_y), QPointF(tip_x - 3, tip_y - 2.5))
        painter.drawLine(QPointF(tip_x, tip_y), QPointF(tip_x - 3, tip_y + 2.5))
    return _glyph(size, draw)


def close_icon(size=14):
    """A close glyph: two strokes, the rescan glyph's weight."""
    def draw(painter, side, _ink):
        inset = side * 0.25
        painter.drawLine(QPointF(inset, inset), QPointF(side - inset, side - inset))
        painter.drawLine(QPointF(side - inset, inset), QPointF(inset, side - inset))
    return _glyph(size, draw)


def dot_icon(filled, ink, size=10):
    """A toggle's state dot: filled when on, a ring when off (the artboard's
    "o Enter autonomous mode" / "* Leave manual mode")."""
    def draw(painter, side, colour):
        painter.setBrush(QColor(colour) if filled else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(2, 2, side - 4, side - 4))
    inks = [(mode, ink) for mode in (QIcon.Mode.Normal, QIcon.Mode.Active)]
    inks.append((QIcon.Mode.Disabled, theme.DISABLED[1]))
    return _glyph(size, draw, inks)


def square_icon(ink, size=10):
    """A small solid square in `ink`: a model's stop state on the rail (L1),
    the severity marks' shape. The same square in every mode - a state, not
    a hover effect."""
    def draw(painter, side, colour):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        inset = side * 0.2
        painter.drawRect(QRectF(inset, inset, side - 2 * inset, side - 2 * inset))
    return _glyph(size, draw, [(mode, ink) for mode in (
        QIcon.Mode.Normal, QIcon.Mode.Active, QIcon.Mode.Selected)])


def disclosure_icon(is_open, size=10):
    """The disclosure's small ink triangle: pointing right when closed, down
    when open; the platform arrow is a heavy chevron on macOS."""
    def draw(painter, side, colour):
        painter.setBrush(QColor(colour))
        painter.setPen(Qt.PenStyle.NoPen)
        path = QPainterPath()
        if is_open:
            path.moveTo(side * 0.2, side * 0.32)
            path.lineTo(side * 0.8, side * 0.32)
            path.lineTo(side * 0.5, side * 0.72)
        else:
            path.moveTo(side * 0.32, side * 0.2)
            path.lineTo(side * 0.72, side * 0.5)
            path.lineTo(side * 0.32, side * 0.8)
        path.closeSubpath()
        painter.drawPath(path)
    inks = [(mode, theme.TEXT) for mode in (QIcon.Mode.Normal, QIcon.Mode.Active)]
    inks.append((QIcon.Mode.Disabled, theme.DISABLED[1]))
    return _glyph(size, draw, inks)


def target_px():
    """A small control's side: at least 24 px (WCAG 2.5.8), growing with the
    font (AUD-12)."""
    return max(24, int(round(StopButton.line_height() * 1.5)))


def lamp_px():
    """A lamp's diameter: 16 px, or four fifths of a line above that."""
    return max(LAMP_PX, int(round(StopButton.line_height() * 0.8)))


def mark(colour, width=None, hollow=False):
    """A severity or fault mark: a small square in `colour` - solid, or hollow
    for a warning (`theme.SEVERITY_MARK_HOLLOW`) - beside the word that says
    the same thing (F14: never colour alone). Its colour is set once here,
    never on a tick."""
    square = QFrame()
    square.setObjectName("mark")
    side = width or theme.SPACE[4] - theme.SPACE[1]
    square.setFixedSize(side, side)
    square.setStyleSheet(mark_sheet(colour, hollow))
    return square


def mark_sheet(colour, hollow=False):
    if hollow:
        return (f"QFrame#mark {{ background-color: transparent; "
                f"border: 2px solid {colour}; border-radius: 1px; }}")
    return (f"QFrame#mark {{ background-color: {colour}; border: none; "
            f"border-radius: 1px; }}")


#: Confirmations waiting for an answer. The stop closes them, answering No,
#: before it acts: an open question never stands between the operator and
#: the stop, and never answers Yes for them afterwards.
_PENDING_CONFIRMS = []


def ask(parent, prompt, title=CONFIRM_WORDS[0], yes=CONFIRM_WORDS[1],
        no=CONFIRM_WORDS[2]):
    """A question that does not block the stop (F1, F17), titled and
    answered by verbs (L14): the title is the question in a few words, the
    prompt says what happens, the buttons say what they do.

    `QMessageBox.question` is application-modal: while one was up, the rail's
    stop could not be pressed. This one is modeless - the rest of the window
    keeps working - and waits in a local event loop so the caller still gets
    a bool. The "no" verb is the default and Escape answers it: Return never
    clears a latch by accident.
    """
    box = QMessageBox(QMessageBox.Icon.NoIcon, title, title,
                      QMessageBox.StandardButton.Yes
                      | QMessageBox.StandardButton.No, parent)
    box.setInformativeText(prompt)
    box.button(QMessageBox.StandardButton.Yes).setText(yes)
    box.button(QMessageBox.StandardButton.No).setText(no)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    box.setEscapeButton(QMessageBox.StandardButton.No)
    box.setWindowModality(Qt.WindowModality.NonModal)
    answered = []
    box.finished.connect(lambda code: answered.append(code))
    _PENDING_CONFIRMS.append(box)
    try:
        box.show()
        # Events are processed until the answer arrives - timers, the rail's
        # tick, a press of the stop - without a nested QEventLoop, which
        # would also run every deferred delete posted outside any loop.
        while not answered:
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.AllEvents
                | QEventLoop.ProcessEventsFlag.WaitForMoreEvents)
    finally:
        if box in _PENDING_CONFIRMS:
            _PENDING_CONFIRMS.remove(box)
    clicked = box.clickedButton()
    answer = (clicked is not None
              and box.standardButton(clicked) == QMessageBox.StandardButton.Yes)
    box.deleteLater()
    return answer


def cancel_pending_confirms():
    """Answer No to every open question - the stop is about to act."""
    for box in list(_PENDING_CONFIRMS):
        box.reject()


class RegionOverlay(QWidget):
    """A drag across the whole virtual desktop to pick a rectangle.

    It reports; it does not write. The old overlay assigned `model.focus_area`
    itself and then raised a `QMessageBox` *over* a frameless, always-on-top,
    translucent window — the dialog could land beneath the overlay with
    application-modal input already blocked, which is PYSIDE-12's deadlock and
    one of the two modals that hung the Qt suite. **Nothing here opens a
    dialog.** A too-small drag re-labels the instruction line instead.

    Qt event-handler names are fixed by Qt and exempt from the naming scheme.
    """

    def __init__(self, on_region, parent=None):
        QWidget.__init__(self, parent)
        self.on_region = on_region
        self.start_point = None
        self.end_point = None
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        geometry = QRect()
        for screen in QApplication.screens():
            geometry = geometry.united(screen.geometry())
        if not geometry.isNull():
            self.setGeometry(geometry)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.instruction_label = QLabel(
            "Click and drag to select the capture region  -  ESC to cancel")
        self.instruction_label.setObjectName("sectionTitle")
        layout.addWidget(self.instruction_label, 0,
                         Qt.AlignmentFlag.AlignTop
                         | Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()

    # -- Qt event handlers ------------------------------------------------
    def showEvent(self, event):
        """Take focus explicitly: a frameless `Qt.Tool` window is often not
        given keyboard focus, and without it Escape never arrives."""
        super().showEvent(event)
        self.setFocus()
        self.activateWindow()
        self.raise_()
        self.setCursor(Qt.CursorShape.CrossCursor)
        events.debug("Region Overlay", f"shown over {self.geometry()}",
                     source="QtView")

    def mousePressEvent(self, event):
        self.start_point = event.globalPosition().toPoint()
        self.end_point = self.start_point
        self.update()

    def mouseMoveEvent(self, event):
        self.end_point = event.globalPosition().toPoint()
        self.update()

    def mouseReleaseEvent(self, event):
        if self.start_point is None or self.end_point is None:
            return self.close()
        left, top, width, height = self._logical_region()
        if width < MIN_REGION_PX or height < MIN_REGION_PX:
            # No QMessageBox over this window, ever (PYSIDE-12).
            self.instruction_label.setText(
                f"That drag was {width}x{height} - too small. Drag at least "
                f"{MIN_REGION_PX}x{MIN_REGION_PX}, or press ESC to cancel.")
            self.start_point = self.end_point = None
            self.update()
            events.debug("Region Rejected", f"{width}x{height} under "
                         f"{MIN_REGION_PX} px", source="QtView")
            return
        region = self._physical_region(left, top, width, height)
        self.close()
        events.debug("Region Picked", f"logical ({left}, {top}) {width}x"
                     f"{height} -> physical {region}", source="QtView")
        if callable(self.on_region):
            self.on_region(*region)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            events.debug("Region Cancelled", "escape", source="QtView")
            self.close()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        tint = QColor(theme.BACKGROUND)
        tint.setAlpha(100)
        painter.fillRect(self.rect(), tint)
        if self.start_point is None or self.end_point is None:
            return
        # The trace colour: a selection is something to read, not a stop.
        pen = QPen(QColor(theme.TRACE))
        pen.setWidth(3)
        painter.setPen(pen)
        origin = self.geometry().topLeft()
        left, top, width, height = self._logical_region()
        painter.drawRect(left - origin.x(), top - origin.y(), width, height)

    # -- helpers -----------------------------------------------------------
    def _logical_region(self):
        left, right = sorted((self.start_point.x(), self.end_point.x()))
        top, bottom = sorted((self.start_point.y(), self.end_point.y()))
        return left, top, right - left, bottom - top

    def _screen_at(self, point):
        """The screen the drag happened on - not the primary one."""
        screen = QApplication.screenAt(QPoint(int(point[0]), int(point[1])))
        return screen or QApplication.primaryScreen()

    def _physical_region(self, left, top, width, height):
        screen = self._screen_at((left, top))
        if screen is None:
            return (left, top, width, height)
        origin = screen.geometry().topLeft()
        return to_physical_pixels(left, top, width, height,
                                  screen.devicePixelRatio(),
                                  screen_origin=(origin.x(), origin.y()))




class EntryHead(QFrame):
    """An entry's head: the name, then (on the overview) "Open" with the
    disclosure chevron, the close button at the right.

    On the overview the whole head is one press target (K4): a click
    anywhere on it but the close button, or Return/Space with it focused,
    emits `pressed`; it takes Tab focus and shows the ink ring. The body
    below is never part of it (it holds controls). On the device page the
    head is inert and takes no focus.
    """

    pressed = Signal()

    def __init__(self, name, parent=None):
        QFrame.__init__(self, parent)
        self.name = name
        self.setObjectName("entryHead")
        self.is_pressable = False
        self._down = False
        self.setAccessibleName(name)
        self.set_pressable(False)

    def set_pressable(self, flag):
        flag = bool(flag)
        self.is_pressable = flag
        QtPanelView._set_prop(self, "pressable", "true" if flag else "false")
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus if flag else Qt.FocusPolicy.NoFocus)
        label = f"{OPEN_WORD} {self.name}"
        self.setToolTip(label if flag else "")
        self.setAccessibleName(label if flag else self.name)
        if flag:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
            if self.hasFocus():
                self.clearFocus()

    def mousePressEvent(self, event):
        if self.is_pressable and event.button() == Qt.MouseButton.LeftButton:
            self._down = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        was, self._down = self._down, False
        if (was and self.is_pressable and event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            event.accept()
            self.pressed.emit()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if self.is_pressable and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                                 Qt.Key.Key_Space):
            event.accept()
            self.pressed.emit()
            return
        super().keyPressEvent(event)


class SheetEntry(QFrame):
    """One open model on the sheet: a 2 px ink rule, its head, its body.

    No card edge: whitespace separates entries. The head carries the name,
    on the overview the "Open" affordance (the head is a press target, K4),
    and a close that says what it does: closing a model stops and
    disconnects it, and the rail reopens it. Under the head, what the model
    must say before anything else - a lost device, a stop that did not
    confirm (a signal rule and "Stop not confirmed. Treat as live."). The
    tier-2 disclosure is not in the head (K3): it is the foot of the body.
    """

    closed = Signal()
    #: The head was pressed on the overview: show this device alone.
    open_requested = Signal()

    def __init__(self, name=None, panel=None, parent=None):
        QFrame.__init__(self, parent)
        self.name = name or ""
        self.panel = panel
        self.is_unconfirmed = False
        self.is_overview = False
        self.setObjectName("entry")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.rule = QFrame()
        self.rule.setObjectName("entryRule")
        self.rule.setFixedHeight(theme.RULE_STRONG_PX)
        layout.addWidget(self.rule)
        # The head's 2 px edge (its focus ring) is matched by the body's
        # inset, so the name still lines up with the captions under it.
        edge = HEAD_EDGE_PX
        self.head = EntryHead(self.name)
        self.head.pressed.connect(self.open_requested.emit)
        head = QHBoxLayout(self.head)
        head.setContentsMargins(0, theme.SPACE[4] - edge, 0, theme.SPACE[3] - edge)
        head.setSpacing(theme.PAD)
        # The name wraps rather than forcing the entry wider (Temperature /
        # Controller at 28 pt); it takes the head's slack, so it wraps only
        # when it must.
        self.title = QLabel(self.name)
        self.title.setObjectName("entryName")
        self.title.setProperty("opened", "false")
        self.title.setWordWrap(True)
        head.addWidget(self.title, 1)
        # "Open" with the disclosure's chevron: a word, not a second button -
        # it takes no focus and no press of its own; the head does.
        self.open_word = QToolButton()
        self.open_word.setObjectName("entryOpen")
        self.open_word.setText(OPEN_WORD)
        self.open_word.setIcon(disclosure_icon(False))
        self.open_word.setIconSize(QSize(theme.SPACE[4] - theme.SPACE[1],
                                         theme.SPACE[4] - theme.SPACE[1]))
        self.open_word.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.open_word.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_word.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.open_word.setVisible(False)
        head.addWidget(self.open_word)
        # What the entry must say first - a lost device, a stop that did not
        # confirm - sits on a line of its own under the name, never squeezed.
        self.lost_mark = mark(theme.SIGNAL)
        self.lost_label = QLabel("")
        self.lost_label.setObjectName("entryNote")
        self.unconfirmed_mark = mark(theme.SIGNAL)
        self.unconfirmed_label = QLabel(UNCONFIRMED_LINE)
        self.unconfirmed_label.setObjectName("entryNote")
        notes = QVBoxLayout()
        notes.setContentsMargins(0, 0, 0, theme.SPACE[3])
        notes.setSpacing(theme.GAP)
        for marker, label in ((self.lost_mark, self.lost_label),
                              (self.unconfirmed_mark, self.unconfirmed_label)):
            label.setWordWrap(True)
            line = _bare_row(marker, label, spacing=theme.PAD)
            line.layout().setAlignment(marker, Qt.AlignmentFlag.AlignVCenter)
            line.layout().setStretch(1, 1)
            line.setVisible(False)
            notes.addWidget(line)
        self._lost_line = notes.itemAt(0).widget()
        self._unconfirmed_line = notes.itemAt(1).widget()
        side = target_px()
        self.close_button = QToolButton()
        self.close_button.setObjectName("iconButton")
        self.close_button.setIcon(close_icon())
        self.close_button.setIconSize(QSize(side // 2, side // 2))
        self.close_button.setFixedSize(side, side)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.close_button.setToolTip(f"Close {self.name} (stops and disconnects it)")
        self.close_button.setAccessibleName(f"Close {self.name}")
        self.close_button.clicked.connect(self.close)
        head.addWidget(self.close_button)
        layout.addWidget(self.head)
        body = QVBoxLayout()
        body.setContentsMargins(edge, 0, edge, 0)
        body.setSpacing(0)
        body.addLayout(notes)
        if panel is not None:
            body.addWidget(panel)
        layout.addLayout(body)
        # A row's entries share one height; the slack goes under the body,
        # never into the head (a taller head drops its name below its row's).
        self.head.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout.addStretch(1)

    def set_opened(self, opened):
        """The opened model's name is one step larger."""
        QtPanelView._set_prop(self.title, "opened", "true" if opened else "false")

    def set_page(self, overview):
        """Draw this entry for a page (K4). The overview: the compact entry,
        tier 1 only, the head a press target with "Open". The device page:
        the model alone, readings `focal`, its disclosures at the foot of the
        body; the head is inert."""
        overview = bool(overview)
        self.is_overview = overview
        self.set_opened(not overview)
        self.head.set_pressable(overview)
        if self.open_word.isHidden() == overview:
            self.open_word.setVisible(overview)
        if self.panel is not None:
            self.panel.set_opened(not overview)
            self.panel.show_tiers(not overview)

    def set_lost(self, text):
        """A lost device, said in the entry's own head."""
        shown = bool(text)
        if self.lost_label.text() != text:
            self.lost_label.setText(text)
        if self._lost_line.isHidden() == shown:
            self._lost_line.setVisible(shown)

    def set_unconfirmed(self, flag):
        """The one entry-level red: this model's stop did not confirm."""
        flag = bool(flag)
        if flag == self.is_unconfirmed:
            return
        self.is_unconfirmed = flag
        self.rule.setStyleSheet(
            f"QFrame#entryRule {{ background-color: {theme.SIGNAL}; }}" if flag else "")
        self._unconfirmed_line.setVisible(flag)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


class RailItem(QPushButton):
    """A model in the rail's list: its name, elided to the rail with the whole
    name as its tooltip; the opened one is checked (highlighted). A latched
    model carries a small ink square before its name, one whose stop did not
    confirm a signal square (L1); the words ride in its tooltip and name."""

    def __init__(self, name, parent=None):
        QPushButton.__init__(self, name, parent)
        self.name = name
        self.stop_mark = None       # None, "stopped" or "unconfirmed"
        self.setObjectName("railModel")
        self.setCheckable(True)
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_stop(self, mark_kind):
        """`None`, "stopped" or "unconfirmed", from `Controller.stop_state`."""
        if mark_kind == self.stop_mark:
            return
        self.stop_mark = mark_kind
        if mark_kind is None:
            self.setIcon(QIcon())
        else:
            side = theme.SPACE[4] - theme.SPACE[1]
            ink = theme.SIGNAL if mark_kind == "unconfirmed" else theme.TEXT
            self.setIcon(square_icon(ink, side))
            self.setIconSize(QSize(side, side))
        self.setAccessibleName(self._spoken())
        self._fit()

    def _spoken(self):
        words = RAIL_STOP_WORDS.get(self.stop_mark)
        return f"{self.name}, {words}" if words else self.name

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def _fit(self):
        icon = self.iconSize().width() + theme.GAP if self.stop_mark else 0
        room = max(1, self.width() - 2 * theme.INSET - theme.GAP - icon)
        shown = self.fontMetrics().elidedText(self.name, Qt.TextElideMode.ElideRight, room)
        if shown != self.text():
            self.setText(shown)
        tip = self._spoken() if (shown != self.name or self.stop_mark) else ""
        if self.toolTip() != tip:
            self.setToolTip(tip)


# ---------------------------------------------------------------------------
# The panel renderer
# ---------------------------------------------------------------------------

class QtPanelView(PanelView, QWidget):
    """One panel, rendered from its schema: an entry's body on the sheet, or
    Setup's table in its dock. Was `QtDynamicView`.

    Tier 1 is laid out straight onto the sheet; tier 2 goes into a
    panel-toned well behind `tier_button` (the model's one disclosure); tier 3
    into a strip behind `diag_button` inside that well. The disclosure is the
    last thing in the tier-1 body, left-aligned, and the well follows it with
    no gap (K3): the press and what it reveals are never a screen apart. Both
    live in `tier_block`, which the overview hides (`show_tiers`).

    Every element type in `schema.ELEMENT_TYPES` has a `_make_` here, which is
    what `PanelView.__init__` checks before it will construct: a renderer that
    cannot draw an element type fails loudly at build time instead of quietly
    skipping the control.
    """

    #: {(model name, tier): open} for the session: a disclosure keeps its
    #: state across a close and a reopen of its model (the brief).
    open_tiers = {}

    def __init__(self, controller, name, panel=None, parent=None):
        QWidget.__init__(self, parent)
        PanelView.__init__(self, controller, name, panel)
        self._widgets = {}          # id(element) -> widget
        self._companions = {}       # id(element) -> a widget greyed with it
        self._detached = {}         # id(element) -> (window, feed), G4
        self._clean_text = {}       # id(element) -> last text we wrote
        self._holders = {}          # id(element) -> what hides when it is normal
        self._lamps = {}            # id(element) -> a tier-1 yes/no readout's lamp
        self._tier_of = {}          # id(element) -> its section's tier
        self._changed_at = {}       # id(element) -> when its value last changed
        self._sliders = {}          # id(element) -> the slider beside the entry
        self._toggle_captions = {}  # id(element) -> the caption shown over a toggle
        self._unit_labels = {}      # id(element) -> the unit beside its readout
        self._base_tips = {}        # id(widget) -> its tooltip while enabled (L3)
        self._reasons = {}          # id(element) -> why it is greyed out, or ""
        self._reason_lines = {}     # id(section frame) -> its "why" caption (L3)
        self._reason_frame = {}     # id(element) -> its section frame
        self._pending = set()       # entries a slider is carrying to the model
        self._overlay = None
        self._table = None          # built on the first layout="row" section
        self._sections = list(self._schema()["sections"])
        # One flag per section, in schema order: `_make_section` is handed a
        # title and a layout; the tier and an action line are decided by the
        # section itself.
        self._action_rows = [is_action_row(s) for s in self._sections]
        self._kinds = reading_kinds(self._schema())
        self._sections_made = 0
        self._building_tier, self._building_title = 1, ""
        self._opened = False
        self._refusals = {}         # id(section frame) -> its refusal line
        self._refusal_shown = None  # the refusal line on screen, if any
        self._acting = None         # the element whose command is running
        self._last_state = None
        self._styled = {}           # id(element) -> the sheet it was given
        self._lost = []             # devices the model reports lost
        self._is_stale = False
        self._frozen = False        # the model is latched: its numbers stop
        self._closed = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(theme.SPACE[5])
        # What the operator must know before reading anything else sits at
        # the top: a lost device, stale readings, and a refusal from a command
        # that has no section.
        self.notice = QLabel("")
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.stale_label = QLabel("Readings are stale")
        self.stale_label.setObjectName("staleLabel")
        self.stale_label.setVisible(False)
        self._panel_refusal = self._refusal_line()
        self._layout.addWidget(self._line_with_mark(self.notice, theme.SIGNAL))
        self._layout.addWidget(self.stale_label)
        self._layout.addWidget(self._panel_refusal)
        self._tier_layouts = {1: QVBoxLayout()}
        self._tier_layouts[1].setSpacing(theme.SPACE[5])
        self._layout.addLayout(self._tier_layouts[1])
        self.tier_button = self.diag_button = None
        self.well = self.diagnostics = None
        self.tier_block = None
        self.well_scroll = None
        self._build_tiers()

        self._build()
        self._order_focus()

        for tier in (2, 3):
            self._set_tier_open(tier, QtPanelView.open_tiers.get((self.name, tier), False))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start(self.REFRESH_MS)
        events.debug("Panel Opened", f"{self.name} rendered "
                     f"{len(self._elements)} elements", source="QtView")

    def _schema(self):
        """The schema, read once: a model builds a fresh dict on every read,
        and the reading kinds and tiers are keyed by the elements' identity."""
        cached = getattr(self, "_schema_once", None)
        if cached is None:
            cached = self._schema_once = PanelView._schema(self)
        return cached

    # -- tiers ----------------------------------------------------------------
    def _build_tiers(self):
        """The disclosure, the well and the diagnostics strip - only for a
        schema that declares a tier past 1 (Setup declares none)."""
        tiers = {tier_of(s) for s in self._sections}
        if not tiers & {2, 3}:
            return
        # K3: the disclosure at the foot of tier 1, left-aligned, the well
        # directly under it (no gap); keyboard order follows, since both are
        # built after tier 1's layout and before nothing else.
        self.tier_block = QWidget()
        self.tier_block.setObjectName("bare")
        block = QVBoxLayout(self.tier_block)
        block.setContentsMargins(0, 0, 0, 0)
        block.setSpacing(0)
        self.tier_button = self._disclosure(disclosure_text(self._sections, 2), 2)
        block.addWidget(self.tier_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.well = QFrame()
        self.well.setObjectName("well")
        inside = QVBoxLayout(self.well)
        pad = theme.SPACE[5]
        inside.setContentsMargins(pad, pad, pad, pad)
        inside.setSpacing(theme.SPACE[5])
        self._tier_layouts[2] = QVBoxLayout()
        self._tier_layouts[2].setSpacing(theme.SPACE[5])
        inside.addLayout(self._tier_layouts[2])
        if 3 in tiers:
            self.diag_button = self._disclosure(disclosure_text(self._sections, 3), 3)
            inside.addWidget(self.diag_button, 0, Qt.AlignmentFlag.AlignLeft)
            self.diagnostics = QFrame()
            self.diagnostics.setObjectName("diagnostics")
            strip = QVBoxLayout(self.diagnostics)
            strip.setContentsMargins(theme.SPACE[5], theme.GAP, 0, theme.GAP)
            strip.setSpacing(theme.SPACE[4])
            self._tier_layouts[3] = strip
            inside.addWidget(self.diagnostics)
        # L5: the well scrolls inside its own area on the device page, so the
        # head and tier 1 above it never scroll away (the dashboard sizes it
        # to the room left, `QtDashboard._fit_well`).
        self.well_scroll = QScrollArea()
        self.well_scroll.setObjectName("wellScroll")
        self.well_scroll.setWidgetResizable(True)
        self.well_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.well_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.well_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.well_scroll.setWidget(self.well)
        self.well.installEventFilter(self)
        self._well_height = None
        block.addWidget(self.well_scroll)
        self._layout.addWidget(self.tier_block)

    def _disclosure(self, text, tier):
        button = QToolButton()
        button.setObjectName("disclosure")
        button.setText(text)
        button.setCheckable(True)
        button.setIcon(disclosure_icon(False))
        button.setIconSize(QSize(theme.SPACE[4] - theme.SPACE[1], theme.SPACE[4] - theme.SPACE[1]))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAccessibleName(f"{text}: {self.name}")
        # "Show or hide the Stepper Probe settings" (QT7-17), whatever the
        # disclosure's own words.
        words = text[len(self.name):].strip() if text.startswith(self.name) else text
        words = {"configure": "settings"}.get(words.lower(), words.lower())
        button.setToolTip(f"Show or hide the {self.name} "
                          f"{'diagnostics' if tier == 3 else words}")
        button.toggled.connect(lambda on, t=tier: self._set_tier_open(t, on))
        return button

    def _order_focus(self):
        """Tab follows the page (K3): every tier-1 control, then the
        disclosure, then what the well holds. The block is built before the
        elements (their tiers' layouts must exist), so the creation order
        alone would put the disclosure first."""
        if self.tier_block is None:
            return
        chain, widget = [], self.nextInFocusChain()
        while widget is not None and widget is not self and len(chain) < 5000:
            if self.isAncestorOf(widget):
                chain.append(widget)
            widget = widget.nextInFocusChain()
        block = self.tier_block
        before = [w for w in chain if w is not block and not block.isAncestorOf(w)]
        inside = [w for w in chain if w is not self.tier_button and block.isAncestorOf(w)]
        order = before + [self.tier_button] + inside
        for first, second in zip(order, order[1:]):
            QWidget.setTabOrder(first, second)

    def tier_is_open(self, tier):
        body = self.well if tier == 2 else self.diagnostics
        return body is not None and not body.isHidden()

    def _set_tier_open(self, tier, is_open):
        button = self.tier_button if tier == 2 else self.diag_button
        body = self.well if tier == 2 else self.diagnostics
        if button is None or body is None:
            return
        is_open = bool(is_open)
        if button.isChecked() != is_open:
            was = button.blockSignals(True)
            button.setChecked(is_open)
            button.blockSignals(was)
        if button.property("open") != is_open:
            button.setProperty("open", is_open)
            button.setIcon(disclosure_icon(is_open))
        body.setVisible(is_open)
        if tier == 2 and self.well_scroll is not None:
            self.well_scroll.setVisible(is_open)
        QtPanelView.open_tiers[(self.name, tier)] = is_open
        window = self.window()
        if not self._closed and hasattr(window, "_schedule_arrange"):
            window._schedule_arrange()

    def well_content_height(self):
        """The well's natural height at the width its scroll area gives it."""
        if self.well is None:
            return 0
        width = max(1, self.well_scroll.viewport().width())
        height = (self.well.heightForWidth(width) if self.well.hasHeightForWidth()
                  else self.well.sizeHint().height())
        return max(height, self.well.minimumSizeHint().height())

    def eventFilter(self, watched, event):     # noqa: N802 - Qt's name
        """The well's contents changed size (a section opened, a line
        wrapped): the dashboard fits the well to its page again (L5)."""
        if (watched is self.well and event.type() == QEvent.Type.LayoutRequest
                and not self._closed and self.tier_is_shown(2)):
            height = self.well_content_height()
            if height != self._well_height:
                self._well_height = height
                window = self.window()
                if hasattr(window, "_schedule_arrange"):
                    window._schedule_arrange()
        return QWidget.eventFilter(self, watched, event)

    # `take_disclosure` is retired (K3, 2026-09-26): the entry's head no
    # longer lifts the disclosure out of the body; it stays above its well.

    def show_tiers(self, shown):
        """The device page shows the disclosure and its well; the overview
        shows neither (K4). The remembered open state is not touched: the
        block is hidden around it, so a trip to the overview and back finds
        the well as it was."""
        if self.tier_block is None:
            return
        shown = bool(shown)
        if self.tier_block.isHidden() == shown:
            self.tier_block.setVisible(shown)

    def tier_is_shown(self, tier):
        """Whether tier `tier` is on screen as far as this panel decides:
        its block shown, its well (and for tier 3 its strip) open."""
        if tier <= 1:
            return True
        if self.tier_block is None or self.tier_block.isHidden():
            return False
        return self.tier_is_open(2) and (tier == 2 or self.tier_is_open(3))

    def set_opened(self, opened):
        """The opened model's axis readings are `focal`, the others `compact`."""
        opened = bool(opened)
        if opened == self._opened:
            return
        self._opened = opened
        size = "focal" if opened else "compact"
        for element in self._elements:
            if self._kinds.get(id(element)) == "axis":
                self._set_prop(self._widget_for(element), "scale", size)

    @property
    def status_label(self):
        """The refusal line on screen, or the panel's own (empty) one."""
        return self._refusal_shown or self._panel_refusal

    @staticmethod
    def _refusal_line():
        line = QLabel("")
        line.setObjectName("refusal")
        line.setWordWrap(True)
        line.setTextFormat(Qt.TextFormat.RichText)
        line.setVisible(False)
        return line

    def _line_with_mark(self, label, colour):
        """`label` with a fault mark beside it; both show and hide as one."""
        holder = QWidget()
        holder.setObjectName("bare")
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(theme.PAD)
        row.addWidget(mark(colour), 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label, 1)
        holder.setVisible(False)
        self._notice_holder = holder
        return holder

    # -- F21: a panel nobody can see does not tick ---------------------------
    def showEvent(self, event):
        super().showEvent(event)
        if not self._closed and not self._timer.isActive():
            self._timer.start(self.REFRESH_MS)
            self._on_timer_tick()

    def hideEvent(self, event):
        super().hideEvent(event)
        if not self._closed and self._timer.isActive():
            self._timer.stop()

    # -- lifecycle ---------------------------------------------------------
    def close(self):
        """Stop the render tick, drop the elements, close the widget.

        Both bases define `close`, so neither is left to the MRO. It closes
        no model and stops no device loop: what a *view* owns is its tick.
        """
        self._closed = True
        alive = qt_alive(self)
        if self._timer is not None and qt_alive(self._timer):
            self._timer.stop()
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None
        # A detached stream's window belongs to the main window, so it would
        # outlive its panel: closed and dropped here.
        for dialog, _ in self._detached.values():
            dialog.close()
            dialog.deleteLater()
        self._detached.clear()
        PanelView.close(self)
        events.debug("Panel Closed", self.name, source="QtView")
        # The entry that held this panel may already have taken it down with
        # it (a parent deletes its children, the timer included): then there
        # is no widget left to close.
        return QWidget.close(self) if alive else True

    def _on_timer_tick(self):
        """The render tick, isolated.

        PYSIDE-10: an unguarded exception in a repeating timer slot reached
        the excepthook, which opened a modal, while the timer kept firing —
        dozens of stacked dialogs a second. The tick reports at most one line
        per second and keeps ticking.
        """
        try:
            self._refresh()
        except Exception as exc:
            events.debug("Refresh Failed", f"{self.name}: {exc}",
                         source="QtView", exception=exc, every=1.0)

    # -- sections ----------------------------------------------------------
    def _make_section(self, title, layout="column"):
        """The container the section's elements are built into, in the layout
        of the section's tier.

        `layout` comes straight from `schema.section(..., layout=)` and is a
        hint every renderer has to honour (Addendum 2): `"row"` lays the
        elements out horizontally, one line per section, in the panel's shared
        table. Setup declares one row section per model type.
        """
        index = self._sections_made
        self._sections_made += 1
        section = self._sections[index] if index < len(self._sections) else {}
        self._building_tier = tier_of(section)
        self._building_title = title
        if layout == "row":
            table = self._panel_table()
            if index < len(self._action_rows) and self._action_rows[index]:
                return table.add_bar(title)
            return table.add_row(title)
        return self._flow_section(title)

    def _tier_layout(self, tier):
        return self._tier_layouts.get(tier) or self._tier_layouts[1]

    def _flow_section(self, title):
        """A section on the sheet: no card and no heading (the captions carry
        the words), its controls in a flow, its refusal line at its foot."""
        frame = QFrame()
        frame.setObjectName("section")
        column = QVBoxLayout(frame)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.GAP)
        holder = QWidget()
        holder.setObjectName("flow")
        flow = FlowLayout(holder)
        column.addWidget(holder)
        column.addWidget(self._reason_caption(frame))
        column.addWidget(self._section_refusal(frame))
        self._tier_layout(self._building_tier).addWidget(frame)
        return FlowSection(flow)

    def _reason_caption(self, frame):
        """L3: a muted caption under a section's row, hidden until one of its
        `go` commands is greyed out; then it says why, as the tooltip does."""
        line = QLabel("")
        line.setObjectName("caption")
        line.setWordWrap(True)
        line.setVisible(False)
        self._reason_lines[id(frame)] = line
        return line

    def reason_line(self, widget):
        """The "why" caption of the section that holds `widget`."""
        while widget is not None:
            line = self._reason_lines.get(id(widget))
            if line is not None:
                return line
            widget = widget.parentWidget()
        return None

    def _section_refusal(self, frame):
        """Every section keeps a refusal line at its foot, hidden until a
        command in that section is refused (F10)."""
        line = self._refusal_line()
        self._refusals[id(frame)] = line
        return line

    def _panel_table(self):
        """One table per panel, built where its first row section appears.

        Every later row section joins it rather than starting a frame of its
        own, which is the only way their columns can line up.
        """
        if self._table is None:
            frame = QFrame()
            frame.setObjectName("table")
            # As wide as its columns and no wider.
            frame.setSizePolicy(QSizePolicy.Policy.Maximum,
                                QSizePolicy.Policy.Maximum)
            outer = QVBoxLayout(frame)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.setSpacing(theme.GAP)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            outer.addLayout(grid)
            outer.addWidget(self._reason_caption(frame))
            outer.addWidget(self._section_refusal(frame))
            grid.setHorizontalSpacing(theme.INSET)
            grid.setVerticalSpacing(theme.PAD)
            grid.setColumnStretch(0, 0)
            self._tier_layout(self._building_tier).addWidget(
                frame, 0, Qt.AlignmentFlag.AlignLeft)
            self._table = PanelTable(grid)
        return self._table

    # -- element builders --------------------------------------------------
    def _label_for(self, container, element):
        """A row keys its column by the schema's own words; the sheet shows
        them in sentence case with the unit split off."""
        text = element.get("text", "")
        if container.is_row:
            return text, ""
        return split_unit(text, element.get("unit"))

    def _make_readonly(self, container, element):
        kind = self._kinds.get(id(element))
        tier = self._building_tier
        caption, unit = self._label_for(container, element)
        if kind and not container.is_row:
            size = ("focal" if self._opened else "compact") if kind == "axis" else kind
            value = ReadingLabel(size)
            letter = axis_letter(element)
            if kind == "axis":
                holder = container.add_axis(sentence_case(self._building_title),
                                            letter, value, unit)
            else:
                holder = container.add(caption, value, unit)
            self._remember(element, value)
            self._holders[id(element)] = holder
            self._remember_unit(element, holder)
            return
        value = ReadoutLabel(EMPTY_READOUT)
        value.setObjectName("valueLabel")
        value.setProperty("quiet", "true")
        value.setProperty("live", "false")
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._remember(element, value)
        if tier == 1 and not container.is_row:
            # A status word on one line; "Yes" is drawn as a lit lamp beside
            # the caption ("* Running"), and a normal value not at all.
            lamp = QLabel("")
            lamp.setObjectName("lamp")
            side = lamp_px() * 3 // 4
            lamp.setFixedSize(side, side)
            lamp.setStyleSheet(f"QLabel#lamp {{ background-color: {theme.TEXT}; "
                               f"border-radius: {side // 2}px; }}")
            lamp.setVisible(False)
            shown = _bare_row(value, _unit(unit)) if unit else value
            self._lamps[id(element)] = lamp
            self._holders[id(element)] = container.add_inline(caption, shown, lead=lamp)
            self._remember_unit(element, shown)
            return
        self._remember_unit(element, container.add(caption, value, unit))

    def _remember_unit(self, element, holder):
        """The unit beside a readout, hidden while the value is the empty
        dash: "— s" reads as a value (L22, QT7-17)."""
        unit = holder.findChild(QLabel, "unit") if isinstance(holder, QWidget) else None
        if unit is not None:
            self._unit_labels[id(element)] = unit

    def _make_entry(self, container, element):
        entry = QLineEdit()
        self._install_validator(entry, element)
        caption, unit = self._label_for(container, element)
        numeric = element.get("value_type") in ("int", "float")
        if container.control_width:
            entry.setMinimumWidth(container.control_width)
        elif numeric:
            entry.setAlignment(Qt.AlignmentFlag.AlignRight
                               | Qt.AlignmentFlag.AlignVCenter)
            entry.setFixedWidth(text_px(NUMBER_CHARS) + theme.SPACE[5])
        else:
            entry.setMinimumWidth(text_px(TEXT_CHARS))
        entry.setAccessibleName(sentence_case(caption))
        self._remember(element, entry)
        travel = element.get("slider")
        if travel and not container.is_row:
            container.add(caption, self._slider_pair(element, entry, travel), unit)
            return
        container.add(caption, entry, unit)

    def _slider_pair(self, element, entry, travel):
        """A slider BESIDE the entry, never instead of it (the brief): the
        slider writes the entry, the entry writes the slider, and a release
        commits the value to the model (`_commit`, the Param validates) so the
        next refresh does not snap it back. Every command still carries the
        entry's text (`_gather_inputs`)."""
        low, high = (int(math.floor(travel[0])), int(math.ceil(travel[1])))
        slider = KeySlider(Qt.Orientation.Horizontal)
        slider.setRange(low, high)
        slider.setSingleStep(max(1, int(round((high - low) / 100.0))))
        slider.setPageStep(max(1, int(round((high - low) / 10.0))))
        slider.setFixedWidth(text_px(SLIDER_CHARS))
        slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        slider.setCursor(Qt.CursorShape.PointingHandCursor)
        slider.setAccessibleName(f"{entry.accessibleName()} slider")
        slider.valueChanged.connect(lambda value: self._on_slider(element, value))
        slider.sliderReleased.connect(lambda: self._commit_slider(element))
        entry.editingFinished.connect(lambda: self._entry_to_slider(element))
        self._sliders[id(element)] = slider
        self._companions[id(element)] = slider
        return _bare_row(slider, entry, spacing=theme.SPACE[4])

    def _on_slider(self, element, value):
        entry = self._widget_for(element)
        if entry is None:
            return
        text = str(int(value))
        if entry.text() != text:
            entry.setText(text)
        slider = self._sliders[id(element)]
        if slider.isSliderDown():
            self._pending.add(id(element))      # committed on release
        else:
            self._commit_slider(element)        # a key or a click on the track

    def _commit_slider(self, element):
        self._pending.discard(id(element))
        entry = self._widget_for(element)
        if entry is None or self._closed:
            return None
        result = self._call("_commit", {element["model_attr"]: entry.text()})
        if result.is_refused:
            self._acting = element
            try:
                self._show_refused(result.reason)
            finally:
                self._acting = None
        return result

    def _entry_to_slider(self, element):
        slider = self._sliders.get(id(element))
        entry = self._widget_for(element)
        if slider is None or entry is None:
            return
        self._set_slider(slider, entry.text())

    @staticmethod
    def _set_slider(slider, text):
        """The slider follows the text, clamped to its travel, silently."""
        try:
            value = int(round(float(text)))
        except (TypeError, ValueError):
            return
        value = max(slider.minimum(), min(slider.maximum(), value))
        if slider.value() != value and not slider.isSliderDown():
            was = slider.blockSignals(True)
            slider.setValue(value)
            slider.blockSignals(was)

    @staticmethod
    def _install_validator(entry, element):
        """The typed gate on what can be entered at all.

        An `int` parameter gets a `QIntValidator`, so the box refuses a
        decimal point rather than accepting one and letting the model round
        it. Everything else numeric gets the wide-decimals `QDoubleValidator`
        of PYSIDE-19. Both take the C locale: a comma-decimal system locale
        rejected "." outright and the operator could not type a number.
        """
        integer = int_bounds(element)
        if integer is not None:
            validator = QIntValidator(integer[0], integer[1], entry)
            validator.setLocale(QLocale.c())
            entry.setValidator(validator)
            return
        bounds = validator_bounds(element)
        if bounds is None:
            return
        low, high, decimals = bounds
        validator = QDoubleValidator(low, high, decimals, entry)
        validator.setLocale(QLocale.c())
        entry.setValidator(validator)

    def _make_button(self, container, element):
        button = QPushButton(sentence_case(element.get("text", "")))
        button.setProperty("role", element.get("role", "neutral"))
        # Values travel with the command (`_gather_inputs` reads the widgets),
        # so PYSIDE-5's "the click read the previous value" cannot recur and
        # no focus is forced anywhere - the named Tk anti-fix.
        button.clicked.connect(lambda: self._run(element))
        self._remember(element, button)
        container.add("", button)

    @staticmethod
    def _names_itself(caption, element):
        """A toggle whose face already says what it is ("Enter autonomous
        mode" under "Autonomous") needs no caption; "Off" under "Sync X" does."""
        first = (caption.split() or [""])[0].lower()
        faces = f"{element.get('true_text', '')} {element.get('false_text', '')}".lower()
        return bool(first) and first in faces

    def _make_toggle(self, container, element):
        caption = sentence_case(element.get("text", ""))
        if DANGER_ROLE in (element.get("on_role"), element.get("off_role")):
            # A model's own stop is a small switch (tier 3): the rail's disc
            # is the stop an operator reaches for, and two red discs would be
            # two stops. The schema's own words are its face and tooltip.
            button = SwitchButton(sentence(element.get("false_text", "")))
            name = sentence(element.get("tooltip") or element.get("false_text", ""))
            button.setToolTip(name)
            button.setAccessibleName(name)
            button.clicked.connect(lambda: self._run_toggle(element))
            self._remember(element, button)
            container.add("", button)
            return
        button = QPushButton(sentence_case(element.get("false_text", "Off")))
        shown = "" if self._names_itself(caption, element) else caption
        # L16: the name carries the visible words (its caption when one is
        # shown, then its face) and the model; `_set_on` keeps it current.
        self._toggle_captions[id(element)] = shown
        button.setAccessibleName(self._toggle_name(shown, button.text()))
        button.clicked.connect(lambda: self._run_toggle(element))
        self._remember(element, button)
        container.add(element.get("text", "") if container.is_row else shown, button)

    def _toggle_name(self, caption, face):
        words = f"{caption}: {face}" if caption else face
        return f"{words}, {self.name}"

    def _make_checkbox(self, container, element):
        """A tick box (G3): the value itself, ticked or not.

        `clicked`, never `toggled`: only the operator's click runs the
        command, and it sends the NEW value read from the model
        (`_run_checkbox`), so a box drawn one refresh behind still flips the
        right way. In a table row the column header is the caption, so the
        box carries no words of its own; its accessible name is the tooltip.
        """
        caption = sentence_case(element.get("text", ""))
        box = TickBox("" if container.is_row else caption)
        name = sentence(element.get("tooltip") or caption)
        box.setAccessibleName(name)
        if element.get("tooltip"):
            box.setToolTip(name)
        box.clicked.connect(lambda *_: self._run_checkbox(element))
        self._remember(element, box)
        container.add(element.get("text", "") if container.is_row else "", box)

    def _make_dropdown(self, container, element):
        combo = MiddleCombo()
        # Sized to a fixed number of characters rather than to its longest
        # option: a column of dropdowns is one width, not four.
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        # In a table row (Setup) a little narrower, so four columns fit a
        # 900 px window without a sideways scroll (L8); it elides the middle.
        combo.setMinimumContentsLength(DROPDOWN_ROW_CHARS if container.is_row
                                       else DROPDOWN_CHARS)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
        caption, _ = self._label_for(container, element)
        combo.setAccessibleName(sentence_case(caption))
        self._remember(element, combo)
        self._reload_options(element, combo)
        combo.currentTextChanged.connect(
            lambda text: self._on_dropdown_changed(element, text))
        # A quiet icon beside the dropdown: the rarely-needed way to pick up a
        # port plugged in after the panel was built, and it says so on hover.
        refresh = QPushButton()
        refresh.setObjectName("iconButton")
        side = target_px()
        refresh.setIcon(reload_icon(side * 7 // 12))
        refresh.setIconSize(QSize(side * 7 // 12, side * 7 // 12))
        refresh.setFixedSize(side, side)
        # L16: one name per rescan - its field, then its row or its model.
        owner = self._building_title if container.is_row else self.name
        rescan = f"Rescan {sentence_case(caption) if not container.is_row else caption} choices"
        refresh.setToolTip(f"{rescan}, {owner}" if owner else rescan)
        refresh.setAccessibleName(f"{rescan}, {owner}" if owner else rescan)
        refresh.clicked.connect(lambda: self._reload_options(element, combo))
        # Greyed with its dropdown: an unticked row offers nothing to press.
        self._companions[id(element)] = refresh
        cell = _bare_row(combo, refresh)
        if container.control_width:
            cell.setMinimumWidth(container.control_width)
        container.add(caption, cell)

    def _make_region_select(self, container, element):
        button = QPushButton(sentence_case(element.get("text", "Select region")))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._pick_region(element))
        value = QLabel(sch.format_region(None))
        value.setObjectName("valueLabel")
        self._remember(element, value)
        container.add("", _bare_row(button, value, spacing=theme.PAD))

    def _make_file_save(self, container, element):
        button = QPushButton(sentence_case(element.get("text", "Save")) + "…")
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._save_to_file(element))
        self._remember(element, button)
        container.add("", button)

    def _make_file_open(self, container, element):
        button = QPushButton(sentence_case(element.get("text", "Open")) + "…")
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._open_from_file(element))
        self._remember(element, button)
        container.add("", button)

    def _make_plot(self, container, element):
        plot = SeriesPlot(role=element.get("role", "neutral"),
                          empty=element.get("empty", ""))
        plot.setToolTip(f"{element.get('x_label', '')} / "
                        f"{element.get('y_label', '')}".strip(" /"))
        self._remember(element, plot)
        container.add_wide(element.get("text", ""), plot)

    def _make_image(self, container, element):
        label = FigureLabel(empty=element.get("empty")
                            or FIGURE_EMPTY.get(element.get("data_command"), ""))
        self._remember(element, label)
        container.add_wide(element.get("text", ""), label)

    def _make_indicator(self, container, element):
        """A lamp beside its caption, never a second copy of the caption."""
        lamp = QLabel("")
        lamp.setObjectName("lamp")
        side = lamp_px()
        lamp.setFixedSize(side, side)
        caption = sentence_case(element.get("text", ""))
        lamp.setAccessibleName(f"{caption}: off")
        lamp.setToolTip(f"{caption}: off")
        self._remember(element, lamp)
        if container.is_row:
            container.add(element.get("text", ""), lamp)
        else:
            container.add_inline(caption, lead=lamp)

    def _make_log_stream(self, container, element):
        if element.get("detached"):
            self._build_detached_log(container, element)
            return
        view = self._log_feed()
        # Fixed, not merely minimum: a QTextEdit takes every spare pixel.
        view.setFixedHeight(LOG_STREAM_PX)
        self._remember(element, view)
        container.add_wide(element.get("text", ""), view)

    @staticmethod
    def _log_feed():
        """The feed itself, attached or in its own window."""
        view = QTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        return view

    # -- G4: a detached log stream -----------------------------------------
    def _build_detached_log(self, container, element):
        """A button in the feed's place ("Gamepad log…"). The feed lives in
        ONE non-modal window, built on the first press and reused after; it
        is polled only while that window shows, per `_wants_data`."""
        caption = sentence_case(element.get("text", ""))
        button = QPushButton(f"{caption}…")
        button.setProperty("role", element.get("role", "neutral"))
        button.setToolTip(f"Open the {caption.lower()} in its own window")
        button.setAccessibleName(f"Open the {caption.lower()}")
        button.clicked.connect(lambda: self.open_detached(element))
        self._remember(element, button)
        container.add("", button)

    def open_detached(self, element):
        """Show the stream's window, building it once; a second press shows
        and raises the same window. It is shown, never run modally, so the
        stop, and every other control, stays one press away."""
        entry = self._detached.get(id(element))
        if entry is None:
            entry = self._detached[id(element)] = self._detached_window(element)
        dialog, _ = entry
        was_open = dialog.isVisible()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        if not was_open:
            # Filled now, not one tick later: it opens on its lines.
            data = self._call(element["source_command"])
            if data.is_ok:
                self._set_data(element, data.value)
        return dialog

    def _detached_window(self, element):
        caption = sentence_case(element.get("text", ""))
        dialog = QDialog(self._main_window())
        dialog.setObjectName("detachedLog")
        dialog.setModal(False)
        # "Stepper Probe gamepad log": no dash (L12, UXPM5-13).
        dialog.setWindowTitle(f"{self.name} {caption.lower()}")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(theme.PAD, theme.PAD, theme.PAD, theme.PAD)
        layout.setSpacing(theme.PAD)
        feed = self._log_feed()
        feed.setAccessibleName(f"{self.name} {caption.lower()}")
        # Empty, it says so (L12): the Web view's first sentence.
        feed.setPlaceholderText("No gamepad input yet." if "gamepad" in caption.lower()
                                else "Nothing logged yet.")
        layout.addWidget(feed)
        close = QPushButton("Close")
        close.setObjectName("logClose")
        close.setProperty("role", "neutral")
        close.setAccessibleName(f"Close the {self.name} {caption.lower()}")
        close.clicked.connect(dialog.reject)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        metrics = QFontMetrics(feed.font())
        dialog.resize(metrics.averageCharWidth() * DETACHED_LOG_CHARS,
                      metrics.lineSpacing() * DETACHED_LOG_LINES)
        # Escape and the window's close both end in `finished` (a QDialog's
        # close is a reject): the window hides and focus returns to the
        # button that opened it (F12).
        dialog.finished.connect(lambda _code: self._return_focus(element))
        button = self._widget_for(element)
        if button is not None:
            # Beside the button that opened it, away from the rail's stop.
            dialog.move(button.mapToGlobal(QPoint(0, button.height())))
        return dialog, feed

    def _main_window(self):
        """The QMainWindow this panel sits in, else its own top window."""
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QMainWindow):
                return widget
            widget = widget.parentWidget()
        return self.window()

    def _return_focus(self, element):
        if self._closed:
            return
        button = self._widget_for(element)
        if button is not None:
            button.setFocus(Qt.FocusReason.OtherFocusReason)

    def detached_window(self, element):
        """The stream's window, or None before the first press."""
        entry = self._detached.get(id(element))
        return entry[0] if entry else None

    def _wants_data(self, element):
        """A detached stream is polled only while its window is showing; a
        plot or figure behind a tier that is not shown (a closed well, or the
        overview, which draws no wells) is not polled at all."""
        if element.get("type") == "log_stream" and element.get("detached"):
            window = self.detached_window(element)
            return window is not None and window.isVisible()
        return self.tier_is_shown(self._tier_of.get(id(element), 1))

    def _make_internal(self, container, element):
        """Registers a command in the schema's allow-list and draws nothing
        (PYSIDE-17: no blank gap for a control that does not exist)."""
        return None

    # -- what base.PanelView calls ----------------------------------------
    def _read_entry(self, element):
        widget = self._widget_for(element)
        return widget.text() if widget is not None else ""

    def _entry_is_dirty(self, element):
        """The operator is mid-edit: refresh must not overwrite the box.

        Dirty means *focused and changed from what we last wrote*, or a
        slider carrying a value the model has not been handed yet, so a
        focused-but-untouched field still follows the model, and an
        unfocused one always does.
        """
        widget = self._widget_for(element)
        if widget is None:
            return False
        changed = widget.text() != self._clean_text.get(id(element), "")
        slider = self._sliders.get(id(element))
        if slider is not None and (slider.isSliderDown()
                                   or (id(element) in self._pending and changed)):
            return True
        if not self._is_focused(widget):
            return False
        return changed

    @staticmethod
    def _is_focused(widget):
        """Does the operator have this widget? A seam, deliberately: which
        widget holds focus is the one thing an offscreen test cannot stage."""
        return widget.hasFocus()

    def _set_text(self, element, text):
        widget = self._widget_for(element)
        if widget is None:
            return
        # An int entry must never be handed "5.000": its QIntValidator will
        # not accept the text the refresh just wrote into it.
        text = display_text(element, text)
        if isinstance(widget, QComboBox):
            self._set_combo_text(widget, text)
            return
        if element["type"] in ("readonly", "region_select"):
            self._show_value(element, widget, text)
            self._clean_text[id(element)] = text
            return
        if widget.text() != text:
            widget.setText(text)
        self._clean_text[id(element)] = text
        slider = self._sliders.get(id(element))
        if slider is not None:
            self._set_slider(slider, text)

    def _show_value(self, element, widget, text):
        """A readout: its value, a dash when empty; ink at rest, the trace
        for a second after it changes, muted when it is a word at rest. In
        tier 1 a normal value is not drawn at all (status by exception) and
        "Yes" is a lit lamp beside the caption."""
        text = as_operator_word(text)
        if self._panel is not None and isinstance(text, str) and not is_number(text):
            # Setup's status words start with a capital, as every line of
            # copy does ("Not scanned yet", "Simulated"; QT7-17).
            text = sentence(text)
        shown = str(text) if str("" if text is None else text).strip() else EMPTY_READOUT
        previous = widget.text()
        if previous != shown:
            widget.setText(shown)
            # Only a number goes live (L19): an identifier that changes every
            # second (the Run ID) is not a measurement.
            if (element["type"] == "readonly" and previous
                    and previous != EMPTY_READOUT and is_number(shown)):
                self._changed_at[id(element)] = time.monotonic()
        self._set_prop(widget, "quiet", "true" if is_quiet_value(text) else "false")
        unit = self._unit_labels.get(id(element))
        if unit is not None and unit.isHidden() == (shown != EMPTY_READOUT):
            unit.setVisible(shown != EMPTY_READOUT)
        changed = self._changed_at.get(id(element))
        live = changed is not None and time.monotonic() - changed < LIVE_S
        self._set_prop(widget, "live", "true" if live else "false")
        holder = self._holders.get(id(element))
        if holder is None or self._tier_of.get(id(element), 1) != 1:
            return
        lamp = self._lamps.get(id(element))
        if lamp is not None:
            is_yes = shown == "Yes"
            if lamp.isHidden() == is_yes:
                lamp.setVisible(is_yes)
                widget.setVisible(not is_yes)
        hidden = self._kinds.get(id(element)) is None and is_normal_value(text)
        if holder.isHidden() != hidden:
            holder.setVisible(not hidden)

    @staticmethod
    def _set_prop(widget, name, value):
        """A dynamic property the sheet selects on, re-polished only when it
        actually changes (F21: no restyle on an idle tick)."""
        if widget is None or widget.property(name) == value:
            return
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_on(self, element, is_on):
        widget = self._widget_for(element)
        if widget is None:
            return
        if isinstance(widget, QCheckBox):
            if widget.isChecked() != is_on:
                # Blocked, so a refresh can never re-send the command.
                was_blocked = widget.blockSignals(True)
                try:
                    widget.setChecked(is_on)
                finally:
                    widget.blockSignals(was_blocked)
            return
        if isinstance(widget, SwitchButton):
            widget.set_on(is_on)
            face = sentence(element.get("true_text" if is_on else "false_text", ""))
            if widget.text() != face:
                widget.setText(face)
                widget.updateGeometry()
            name = sentence(element.get("tooltip_on" if is_on else "tooltip")
                            or element.get("tooltip") or face)
            if widget.toolTip() != name:
                widget.setToolTip(name)
                widget.setAccessibleName(name)
            return
        if element["type"] == "toggle":
            colours = theme.toggle_colors(element, is_on)
            disabled_bg, disabled_fg = theme.DISABLED
            radius = theme.RADIUS["control"]
            # A selector, not bare declarations, so hover, focus and the
            # disabled state still reach a toggle.
            sheet = (f"QPushButton {{ background-color: {colours['background']}; "
                     f"color: {colours['foreground']}; "
                     f"border: 1px solid {colours['border']}; "
                     f"border-radius: {radius}px; }}"
                     f"QPushButton:hover {{ border-color: {theme.MUTED}; }}"
                     f"QPushButton:focus {{ border: {FOCUS_RING}; }}"
                     f"QPushButton:disabled {{ background-color: {disabled_bg}; "
                     f"color: {disabled_fg}; border: 1px dashed {theme.MUTED}; }}")
            if self._styled.get(id(element)) != sheet:
                widget.setIcon(dot_icon(is_on, colours["foreground"]))
            self._restyle(element, widget, sheet)
            wanted = sentence_case(element.get("true_text" if is_on
                                               else "false_text", ""))
            if widget.text() != wanted:
                widget.setText(wanted)
                widget.setAccessibleName(self._toggle_name(
                    self._toggle_captions.get(id(element), ""), wanted))
            return
        # An indicator: a lamp, and no text at all.
        fill, ring = lamp_colours(element, is_on)
        self._restyle(element, widget,
                      f"background-color: {fill}; border: 2px solid {ring}; "
                      f"border-radius: {widget.width() // 2}px;")
        caption = sentence_case(element.get("text", ""))
        name = f"{caption}: {'on' if is_on else 'off'}"
        if widget.accessibleName() != name:
            widget.setAccessibleName(name)
            widget.setToolTip(name)

    def _restyle(self, element, widget, sheet):
        """Hand a widget a sheet only when it differs from the last one. Every
        call re-polishes the widget, and the tick made 94-154 of them a second
        with nothing changing (F21, AUD-9)."""
        if self._styled.get(id(element)) != sheet:
            self._styled[id(element)] = sheet
            widget.setStyleSheet(sheet)

    def _set_data(self, element, data):
        kind = element["type"]
        if kind == "plot":
            self._redraw_plot(element, data)
        elif kind == "log_stream":
            self._refresh_log(element, data)
        elif kind == "image":
            self._redraw_image(element, data)

    def _set_enabled(self, element, is_enabled):
        for widget in (self._widget_for(element),
                       self._companions.get(id(element))):
            if widget is not None and widget.isEnabled() != is_enabled:
                widget.setEnabled(is_enabled)
        self._show_reason(element, is_enabled)

    def _show_reason(self, element, is_enabled):
        """L3: a greyed-out command says why - the gate's reason as its
        tooltip, and for a `go` command a muted caption under its row. The
        words are `gate_reason`'s; they go when the command is live again."""
        widget = self._widget_for(element)
        if not isinstance(widget, QAbstractButton):
            return
        state = self._last_state or {}
        reason = "" if is_enabled else gate_reason(
            element, state.get("mode"), state.get("values") or {}, self._caption_of)
        if self._reasons.get(id(element)) == reason:
            return
        self._reasons[id(element)] = reason
        base = self._base_tips.setdefault(id(widget), widget.toolTip())
        widget.setToolTip(reason or base)
        if element.get("role") == "go":
            self._sync_reason_line(widget)

    def _sync_reason_line(self, widget):
        line = self.reason_line(widget)
        if line is None:
            return
        words = []
        for other in self._elements:
            if other.get("role") != "go":
                continue
            said = self._reasons.get(id(other))
            if said and said not in words and self.reason_line(
                    self._widget_for(other)) is line:
                words.append(said)
        text = "; ".join(words)
        if line.text() != text:
            line.setText(text)
        if line.isHidden() == bool(text):
            line.setVisible(bool(text))

    def _caption_of(self, attr):
        for element in self._elements:
            if element.get("model_attr") == attr:
                return sentence_case(element.get("text", ""))
        return ""

    def _set_stale(self, is_stale):
        self._is_stale = bool(is_stale)
        self.stale_label.setVisible(self._is_stale and not self._lost)
        self._sync_dim()

    def _sync_dim(self):
        """Numbers go muted when the readings are stale, a device is lost, or
        the model is latched: in each case a number on screen is no longer
        what the hardware is doing."""
        dim = "true" if (self._is_stale or self._lost or self._frozen) else "false"
        for element in self._elements:
            if element["type"] == "plot":
                self._widget_for(element).set_frozen(dim == "true")
        if self.property("stale") == dim:
            return
        self.setProperty("stale", dim)
        # A descendant selector is only re-read when the *descendant* is
        # polished: polishing the panel alone left every readout coloured.
        for widget in [self] + [w for w in self._widgets.values()
                                if isinstance(w, QLabel)]:
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    # -- F3: a lost device ---------------------------------------------------
    def _state(self):
        state = PanelView._state(self)
        self._last_state = state
        return state

    def _refresh(self):
        PanelView._refresh(self)
        state = self._last_state or {}
        # The latch freezes the numbers: a model's state says so at the top
        # level (`Model.state`), a panel whose stop is a schema toggle in its
        # values; either one is enough.
        values = state.get("values") or {}
        frozen = bool(state.get("is_estopped")) or values.get("is_estopped") in (True, "True")
        if frozen != self._frozen:
            self._frozen = frozen
            self._set_prop(self, "frozen", "true" if frozen else "false")
            self._sync_dim()
        self._set_lost(lost_devices(state))

    _sync_gates = _refresh

    @property
    def lost_devices(self):
        return list(self._lost)

    def _set_lost(self, lost):
        """The model says a device is lost: say so at the top of the panel,
        in ink beside a fault mark, and dim every number. The entry's head and
        the rail carry the same news (`QtDashboard._sync_states`)."""
        if lost == self._lost:
            return
        self._lost = list(lost)
        if lost:
            self.notice.setText(
                f"{lost_sentence(self.name, lost)}. Its readings are frozen. "
                f"Press Stop, check the cable, then relaunch from Setup.")
        self._notice_holder.setVisible(bool(lost))
        self.stale_label.setVisible(self._is_stale and not lost)
        self._sync_dim()
        if lost:
            events.debug("Device Lost Shown", f"{self.name}: {lost}",
                         source="QtView")

    def _confirm(self, prompt):
        return ask(self, prompt)

    # -- F10: a refusal is shown where it happened ---------------------------
    def _run(self, element, args=()):
        self._acting = element
        try:
            return PanelView._run(self, element, args)
        finally:
            self._acting = None

    def _refusal_for(self, element):
        """The refusal line of the section that holds this element's control,
        or the panel's own line when it has none."""
        widget = self._widget_for(element) if element is not None else None
        while widget is not None:
            line = self._refusals.get(id(widget))
            if line is not None:
                return line
            widget = widget.parentWidget()
        return self._panel_refusal

    def _show_refused(self, reason):
        """A refusal is a line in the section of the control that caused it,
        not a dialog (F10, HC-5). It is scrolled into view, and the next
        command that succeeds clears it."""
        line = self._refusal_for(self._acting) if reason else None
        if self._refusal_shown is not None and self._refusal_shown is not line:
            self._refusal_shown.setText("")
            self._refusal_shown.setVisible(False)
            self._refusal_shown = None
        if not reason:
            return
        word = html.escape("Refused")
        line.setText(f'<span style="color:{theme.SEVERITY_MARK["warning"]}; '
                     f'font-weight:600">{word}</span>&nbsp;&nbsp;'
                     f"{html.escape(str(reason))}")
        line.setVisible(True)
        self._refusal_shown = line
        QTimer.singleShot(0, lambda: self._scroll_to(line))
        events.debug("Refused Shown", f"{self.name}: {reason}", source="QtView")

    def _scroll_to(self, widget):
        """Bring `widget` into the sheet's (or Setup's) scroll area's view."""
        holder = self.parentWidget()
        while holder is not None and not isinstance(holder, QScrollArea):
            holder = holder.parentWidget()
        if holder is not None and widget.isVisible():
            holder.ensureWidgetVisible(widget, 0, theme.PAD)

    def _apply_theme(self):
        """One sheet, on the QApplication (PYSIDE-15): message boxes and file
        dialogs are themed too."""
        install_font_fallbacks()
        sheet = stylesheet()
        app = QApplication.instance()
        if app is not None:
            if app.styleSheet() != sheet:
                app.setStyleSheet(sheet)
        else:
            self.setStyleSheet(sheet)

    # -- composites --------------------------------------------------------
    def _redraw_plot(self, element, data):
        widget = self._widget_for(element)
        if widget is not None:
            widget.set_series(data)

    def _refresh_log(self, element, data):
        if element.get("detached"):
            entry = self._detached.get(id(element))
            widget = entry[1] if entry else None
        else:
            widget = self._widget_for(element)
        if widget is None:
            return
        lines = data if isinstance(data, (list, tuple)) else str(data or "").splitlines()
        text = "\n".join(str(line) for line in list(lines)[-200:])
        if widget.toPlainText() != text:
            widget.setPlainText(text)
            # `QTextCursor.MoveOperation.End`, not `QTextCursor.End`: the
            # latter is not an instance attribute in PySide6.
            widget.moveCursor(QTextCursor.MoveOperation.End)

    def _redraw_image(self, element, data):
        widget = self._widget_for(element)
        if widget is None:
            return
        if not data:
            # Nothing to draw: the pane is its one-line empty state (L15).
            if not widget.is_empty:
                widget.set_figure(None)
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(bytes(data)):
            widget.set_figure(pixmap)
        else:
            events.debug("Image Rejected", f"{self.name}: "
                         f"{len(bytes(data))} bytes would not decode",
                         source="QtView", every=5.0)

    def _pick_region(self, element):
        """Hand the overlay a callback; the *model* owns the command."""
        self._overlay = RegionOverlay(
            lambda x, y, width, height: self._run(element,
                                                  args=(x, y, width, height)))
        self._overlay.show()

    def _save_to_file(self, element):
        """Ask where, run the command, copy what the model wrote there.

        The model writes its own file (and any sidecar) and returns the path;
        the view moves a copy to the operator's destination. That keeps the
        writing in the model, where the Web client reaches it too.
        """
        extensions = element.get("extensions", ("csv",))
        path, _ = QFileDialog.getSaveFileName(
            self, element.get("text", "Save"), "", dialog_filter(extensions))
        if not path:
            return
        path = with_extension(path, extensions)
        result = self._run(element)
        if not result.is_ok or not result.value:
            return
        source = str(result.value)
        try:
            if os.path.abspath(source) != os.path.abspath(path):
                shutil.copyfile(source, path)
        except OSError as exc:
            events.error("Save Failed", f"could not copy {source} to {path}: "
                         f"{exc}", source="QtView", exception=exc)
            return
        events.info("Saved", f"{self.name} saved to {path}", source="QtView")

    def _open_from_file(self, element):
        extensions = element.get("extensions", ("csv",))
        path, _ = QFileDialog.getOpenFileName(
            self, element.get("text", "Open"), "", dialog_filter(extensions))
        if path:
            self._run(element, args=(path,))

    def _on_dropdown_changed(self, element, text):
        """A selection the operator made. Programmatic writes block signals,
        so this can never be a refresh echoing itself back as a command."""
        if text:
            self._run(element, args=(text,))

    def _reload_options(self, element, combo):
        command = element.get("options_command")
        options = []
        if command:
            try:
                options = [str(option) for option in self._options(command)]
            except Exception as exc:
                events.debug("Options Failed", f"{self.name}.{command}: {exc}",
                             source="QtView", exception=exc, every=5.0)
        current = combo.currentText()
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(options)
            for index, option in enumerate(options):
                combo.setItemData(index, option, Qt.ItemDataRole.ToolTipRole)
            if current in options:
                combo.setCurrentText(current)
        finally:
            combo.blockSignals(False)
        combo._sync_tip(combo.currentText())

    def _set_combo_text(self, combo, text):
        """`current_text` returns "" for an absent value, and "" must never
        become a selectable option named "None" (schema v2's note)."""
        if not text or combo.currentText() == text:
            return
        combo.blockSignals(True)
        try:
            if combo.findText(text) < 0:
                combo.addItem(text)
                combo.setItemData(combo.count() - 1, text,
                                  Qt.ItemDataRole.ToolTipRole)
            combo.setCurrentText(text)
        finally:
            combo.blockSignals(False)
        combo._sync_tip(combo.currentText())

    # -- plumbing ----------------------------------------------------------
    def _remember(self, element, widget):
        self._widgets[id(element)] = widget
        self._tier_of[id(element)] = self._building_tier

    def _widget_for(self, element):
        return self._widgets.get(id(element))

    @staticmethod
    def _row(*widgets):
        return _bare_row(*widgets)


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

class QtDashboard(Dashboard, QMainWindow):
    """The dashboard window: the rail, the sheet, Setup, the tray.

    Absorbs `DashboardWindow`, `QtEventLogPanel` and `QtErrorPopupManager`.

        +- rail -------+- Setup (top dock; put away on launch) --------------+
        | Transfer     | Every model is stopped.   (only when latched)       |
        | stage        | == Stepper Probe > Open  == DC Probe > Open  == ... |
        |  ( Stop )    |  X 1 184  Y -352  Z 20      X 0  Y 0  Z 0           |
        | Stop: Ctrl+. | == Rotator > Open ======  == Red Percent > Open ==== |
        | Overview     |                                                     |
        | model list   |                                                     |
        | Setup  Quit  +- tray: the latest warning or error ---- Show events-+

    The rail is a fixed left dock (nothing can cover it or push the stop off
    screen); the sheet is the central widget, one scroll for the page.

    The sheet has two pages (K4). The overview (`_shown is None`): every
    model's compact entry in the grid, tier 1 only, its head a press target.
    A device page (`_shown` a model's name): that entry alone, full width,
    readings `focal`, its disclosures at the foot of its body. The rail is
    the navigation: "Overview" first, then the models, the shown page
    checked. Launch, and closing the shown device, return to the overview.
    """

    #: Every cross-thread hop goes through this, queued. See the module
    #: docstring: AutoConnection is what PYSIDE-21 was.
    _marshalled = Signal(object)

    STOP_REFRESH_MS = 250

    def __init__(self, controller, setup):
        QMainWindow.__init__(self)
        Dashboard.__init__(self, controller, setup)
        self._entries = {}          # name -> SheetEntry
        self._panels = {}           # name -> QtPanelView inside its entry
        self._is_focused = True
        self._setup_dock = None
        self._setup_scroll = None
        self.setup_view = None
        self.setup_action = None
        self._rail_items = {}       # name -> RailItem
        self._closed_shown = None
        self.reopen_buttons = {}
        self._lost = {}             # name -> the devices it reports lost
        self._alerts = []           # errors waiting to be acknowledged
        self._raise_on_add = None   # a model being reopened
        self._shown = None          # the device page's model; None: the overview
        self._arrangement = None    # what the sheet's grid last laid out
        self._arrange_pending = False
        self._last_event = None
        self._latest_title = None   # the title of the tray's latest line
        self._words = None          # the stop's words last drawn (`stop_words`)
        self._narrow = None
        self._quit_asked = False    # a Quit already answered yes

        self.setWindowTitle("Transfer stage")
        self.resize(1400, 900)
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks)
        # The rail owns the left edge top to bottom; Setup sits beside it.
        self.setCorner(Qt.Corner.TopLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.setCorner(Qt.Corner.BottomLeftCorner, Qt.DockWidgetArea.LeftDockWidgetArea)
        self._marshalled.connect(self._on_marshalled, Qt.QueuedConnection)
        install_font_fallbacks()
        allow_tab_to_every_control()

        self._build_rail()
        self._build_sheet()
        self._sync_rail_width()

        self._stop_timer = QTimer(self)
        self._stop_timer.timeout.connect(self._on_rail_tick)
        self._stop_timer.start(self.STOP_REFRESH_MS)

    # -- lifecycle ---------------------------------------------------------
    @staticmethod
    def ensure_application(argv=None):
        """The one QApplication, created on the launching (main) thread
        before any widget. `app.launch()` calls this before constructing
        the dashboard; tests create their own."""
        application = QApplication.instance()
        if application is None:
            application = QApplication(list(argv or [sys.argv[0]]))
        allow_tab_to_every_control()
        return application

    def wait(self):
        """Run the Qt event loop until the window closes. Blocks the launching
        thread like Tk's mainloop; `close()` runs on aboutToQuit."""
        application = QApplication.instance()
        if application is None:
            return 0
        return getattr(application, "exec")()

    def open(self):
        """Subscribe, show the Setup panel, show the window.

        Setup is a `Panel` the Controller does not own, so it is passed to the
        panel view directly. There is no separate wizard window and no second
        copy of the device list: one setup, rendered like everything else.
        """
        Dashboard.open(self)
        # A plain QDockWidget: closing Setup puts it away, it does not close a
        # model, and the panel behind it keeps the scan results. Closable is
        # load-bearing: Qt *disables* `toggleViewAction` on a dock that cannot
        # be closed, so without it the rail's Setup is dead.
        self._setup_dock = QDockWidget("Setup", self)
        self._setup_dock.setObjectName("setupDock")
        self._setup_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.setup_view = QtPanelView(self.controller, "Setup", panel=self.setup)
        margin = theme.SPACE[7] if self._narrow else theme.SPACE[9]
        self.setup_view.layout().setContentsMargins(margin, theme.GAP, margin,
                                                    theme.SPACE[9])
        # Capped and scrolled (H3): eight rows at 28 pt must not push the
        # sheet off the window.
        self._setup_scroll = QScrollArea()
        self._setup_scroll.setObjectName("setupScroll")
        self._setup_scroll.setWidgetResizable(True)
        self._setup_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._setup_scroll.setWidget(self.setup_view)
        self._setup_dock.setWidget(self._setup_scroll)
        self._setup_dock.setTitleBarWidget(self._build_setup_title())
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, self._setup_dock)
        self._sync_setup_height()
        # `toggleViewAction` rather than a button of our own: the check mark
        # and the dock's visibility are then one piece of state.
        self.setup_action = self._setup_dock.toggleViewAction()
        self.setup_action.setText("Setup")
        self.setup_action.setToolTip("Show the Setup panel to refresh the scan "
                                     "or relaunch")
        self.setup_action.toggled.connect(lambda _: self._sync_empty_state())
        self.setup_button.setDefaultAction(self.setup_action)
        self.setup_button.setVisible(True)

        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.close)

        for name in self.controller.model_names:
            self._add_panel(name)
        if self.controller.model_names:
            # Models built before the window existed (a remembered config, a
            # relaunch): the system has launched, so the wizard gives way here
            # too. `base.Dashboard` only sees the *first add after* this call.
            self._launched = True
            self._collapse_setup()
        self._sync_rail()
        self._sync_stop_button()
        self._sync_states()
        self._sync_empty_state()
        self._set_tray_open(False)
        self.show()
        # Nothing is handed focus at boot: a ring on the first control in the
        # tab order before anything has happened is noise.
        self.rail.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.rail.setFocus()
        events.debug("View Opened", "Qt dashboard shown with the Setup panel",
                     source="QtView")

    def close(self):
        """`Dashboard.close` first (unsubscribe, then the Controller), then
        the window. Both bases define `close`; neither is left to the MRO."""
        if not self._closing:
            Dashboard.close(self)
            events.debug("View Closed", "Qt dashboard closed", source="QtView")
        return QMainWindow.close(self)

    def _ask_quit(self):
        """L9: Quit and the window's close ask first, in the Tk and Web words
        (it stops every model, closes every port and exits); "Stay" keeps
        the station running."""
        if self._closing or self._quit_asked:
            return True
        self._quit_asked = ask(self, QUIT_PROMPT, *QUIT_WORDS)
        return self._quit_asked

    def _on_quit_clicked(self):
        if self._ask_quit():
            self.close()

    def closeEvent(self, event):
        if not self._closing and not self._ask_quit():
            event.ignore()
            return
        if not self._closing:
            Dashboard.close(self)
            events.debug("View Closed", "closeEvent", source="QtView")
        for panel in list(self._panels.values()):
            panel.close()
        self._panels.clear()
        self._entries.clear()
        if self._stop_timer is not None:
            self._stop_timer.stop()
        event.accept()

    # -- threads -----------------------------------------------------------
    def _marshal(self, fn):
        self._marshalled.emit(fn)

    def _on_marshalled(self, fn):
        try:
            fn()
        except Exception as exc:
            events.debug("Marshalled Call Failed", str(exc), source="QtView",
                         exception=exc, every=1.0)

    # -- the rail: title, the stop, the model list, Setup and Quit -----------
    def _build_rail(self):
        """A fixed left dock, the panel tone, full height: the title and the
        simulation line; A's disc with "Stop: Ctrl+." under it; the model list
        (a press brings a model to the top of the sheet); closed models, one
        press from reopening; Setup and Quit at the foot. No pop-up is ever
        placed over it (F1)."""
        self.rail = QFrame()
        self.rail.setObjectName("rail")
        column = QVBoxLayout(self.rail)
        column.setContentsMargins(theme.SPACE[6], theme.SPACE[7],
                                  theme.SPACE[6], theme.SPACE[6])
        column.setSpacing(theme.GAP)
        title = QLabel("Transfer stage")
        title.setObjectName("railTitle")
        title.setWordWrap(True)
        column.addWidget(title)
        self.rail_status = NoteLabel()
        self.rail_status.setObjectName("caption")
        column.addWidget(self.rail_status)
        column.addSpacing(theme.SPACE[5])

        self.stop_button = StopButton(self.rail)
        self.stop_button.clicked.connect(self._on_stop_clicked)
        column.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.stop_hint = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle, min_chars=6)
        self.stop_hint.setObjectName("caption")
        self.stop_hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.stop_hint.set_full_text(RAIL_STOP_HINT)
        column.addWidget(self.stop_hint)
        # The stop from anywhere in the application, dialogs included; it only
        # ever stops (F9).
        self.stop_shortcut = QShortcut(QKeySequence(STOP_SHORTCUT), self)
        self.stop_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.stop_shortcut.activated.connect(self._on_stop_shortcut)
        # Latched: a red square and what happened, under the disc.
        self.latched_mark = mark(theme.SIGNAL)
        self.latched_label = QLabel("")
        self.latched_label.setObjectName("railLatched")
        self.latched_label.setWordWrap(True)
        self.latched_row = _bare_row(self.latched_mark, self.latched_label,
                                     spacing=theme.PAD)
        self.latched_row.layout().setAlignment(self.latched_mark,
                                               Qt.AlignmentFlag.AlignTop)
        self.latched_row.setVisible(False)
        column.addSpacing(theme.SPACE[3])
        column.addWidget(self.latched_row)
        column.addSpacing(theme.SPACE[5])

        # The model list scrolls within the rail, so at 28 pt with eight
        # models the disc above it is never pushed off.
        holder = QWidget()
        holder.setObjectName("railList")
        stack = QVBoxLayout(holder)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(theme.SPACE[0])
        # The page list: "Overview" first (K4), then one item per model.
        self.overview_item = RailItem(OVERVIEW)
        self.overview_item.clicked.connect(lambda _=False: self.show_overview())
        stack.addWidget(self.overview_item)
        self._rail_list = QVBoxLayout()
        self._rail_list.setSpacing(theme.SPACE[0])
        stack.addLayout(self._rail_list)
        self._reopen_holder = QWidget()
        self._reopen_holder.setObjectName("bare")
        self._reopen_layout = QVBoxLayout(self._reopen_holder)
        self._reopen_layout.setContentsMargins(0, theme.SPACE[5], 0, 0)
        self._reopen_layout.setSpacing(theme.SPACE[0])
        self._reopen_holder.setVisible(False)
        stack.addWidget(self._reopen_holder)
        stack.addStretch(1)
        scroll = QScrollArea()
        scroll.setObjectName("railScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        column.addWidget(scroll, 1)

        foot = QHBoxLayout()
        foot.setSpacing(theme.PAD)
        # The one reopen control for Setup. Its action arrives with the dock
        # when the window opens; until then there is nothing to reopen.
        self.setup_button = QToolButton()
        self.setup_button.setObjectName("ghost")
        self.setup_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setup_button.setVisible(False)
        foot.addWidget(self.setup_button)
        self.quit_button = QPushButton("Quit")
        self.quit_button.setObjectName("quiet")
        self.quit_button.setToolTip("Close the station (stops every model)")
        self.quit_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.quit_button.clicked.connect(self._on_quit_clicked)
        foot.addWidget(self.quit_button)
        foot.addStretch(1)
        column.addLayout(foot)

        self.rail_dock = QDockWidget("Transfer stage", self)
        self.rail_dock.setObjectName("railDock")
        self.rail_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        self.rail_dock.setTitleBarWidget(QWidget())
        self.rail_dock.setWidget(self.rail)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.rail_dock)

    def _sync_rail_width(self):
        """248 px, 200 px under 1000 px wide (the brief); a larger launch font
        widens it a little, and the disc shrinks to the narrow size with it."""
        narrow = self.width() < NARROW_WINDOW_PX
        growth = min(max(theme.FONT_SIZE / 12.0, 1.0), RAIL_GROWTH)
        width = int(round((RAIL_NARROW_PX if narrow else RAIL_PX) * growth))
        if self.rail.minimumWidth() != width or self.rail.maximumWidth() != width:
            self.rail.setFixedWidth(width)
        room = width - 2 * theme.SPACE[6] - 2 * (StopButton.FOCUS_GAP + StopButton.FOCUS_PX)
        diameter = min(theme.STOP["diameter_narrow" if narrow else "diameter"], room)
        if self.stop_button.diameter != diameter:
            self.stop_button.set_diameter(diameter)
        if narrow != self._narrow and hasattr(self, "sheet"):
            # A narrow window keeps 24 px of sheet margin, not 32, so two
            # entries still fit side by side at 900 px (the Narrow artboard).
            side = theme.SPACE[7] if narrow else theme.SPACE[9]
            margins = self.sheet.layout().contentsMargins()
            self.sheet.layout().setContentsMargins(side, margins.top(), side,
                                                   margins.bottom())
            if self.setup_view is not None:
                # Setup's table too: no sideways scroll at 900 px (L8).
                margins = self.setup_view.layout().contentsMargins()
                self.setup_view.layout().setContentsMargins(
                    side, margins.top(), side, margins.bottom())
        self._narrow = narrow

    def _build_setup_title(self):
        """Setup's own title bar (L19, QT7-16): its name, and a real way to
        put it away - a command, not an 8 px glyph. The rail's Setup brings
        it back."""
        bar = QWidget()
        bar.setObjectName("bare")
        row = QHBoxLayout(bar)
        side = theme.SPACE[9]
        row.setContentsMargins(side, theme.PAD, side, 0)
        row.setSpacing(theme.PAD)
        title = QLabel("Setup")
        title.setObjectName("dockTitle")
        row.addWidget(title)
        row.addStretch(1)
        self.setup_close = QPushButton("Put away")
        self.setup_close.setObjectName("ghost")
        self.setup_close.setToolTip("Put Setup away (the rail's Setup brings it back)")
        self.setup_close.setAccessibleName("Put Setup away")
        self.setup_close.clicked.connect(lambda _=False: self._setup_dock.close())
        row.addWidget(self.setup_close)
        return bar

    def _sync_setup_height(self):
        """L8 (QT7-3): while the sheet is empty Setup takes the height it
        needs - there is nothing under it to push away. With models on the
        sheet it is capped (H3: eight rows at 28 pt must not push the sheet
        off the window) and scrolls."""
        scroll = self._setup_scroll
        if scroll is None or self.setup_view is None:
            return
        line = StopButton.line_height()
        if not self._entries:
            view = self.setup_view
            width = max(1, scroll.viewport().width())
            need = (view.heightForWidth(width) if view.hasHeightForWidth()
                    else view.sizeHint().height())
            need = max(need, view.minimumSizeHint().height()) + 2 * scroll.frameWidth()
            # Leave the empty sheet its two lines and the tray its one.
            room = max(line * 8, self.height() - line * 8)
            least, cap = min(need, room), room
        else:
            least, cap = 0, max(line * 8, int(self.height() * 0.55))
        if scroll.minimumHeight() != least:
            scroll.setMinimumHeight(least)
        if scroll.maximumHeight() != cap:
            scroll.setMaximumHeight(cap)

    def _on_rail_tick(self):
        """The rail's render tick, isolated like a panel's (PYSIDE-10)."""
        try:
            self._sync_stop_button()
            self._sync_rail()
            self._sync_states()
        except Exception as exc:
            events.debug("Rail Refresh Failed", str(exc), source="QtView",
                         exception=exc, every=1.0)

    def _sync_rail(self):
        """One list item per open model, a Reopen line per closed one. Rebuilt
        only when the set of models changes."""
        names = list(self.controller.model_names)
        if names != list(self._rail_items):
            for item in self._rail_items.values():
                self._rail_list.removeWidget(item)
                item.deleteLater()
            self._rail_items = {}
            for name in names:
                item = RailItem(name)
                item.clicked.connect(lambda _=False, n=name: self.open_entry(n))
                self._rail_list.addWidget(item)
                self._rail_items[name] = item
        for name, item in self._rail_items.items():
            if item.isChecked() != (name == self._shown):
                item.setChecked(name == self._shown)
        if self.overview_item.isChecked() != (self._shown is None):
            self.overview_item.setChecked(self._shown is None)
        closed = [n for n in self.controller.closed_names if n not in names]
        if closed != self._closed_shown:
            self._closed_shown = closed
            self._build_reopen(closed)

    def _rail_status_text(self, names, states):
        lost = [lost_sentence(n, self._lost.get(n)) for n in names
                if self._lost.get(n)]
        if lost:
            return "; ".join(lost)
        if names:
            return simulation_line(states)
        try:
            scanning = bool(self.setup.state.get("is_scanning"))
        except Exception:
            scanning = False
        return "Scanning ports" if scanning else "Nothing running"

    def _sync_states(self):
        """What the models' states say, on the rail and the sheet: a lost
        device, the latch, a stop that did not confirm, the simulation line."""
        names = list(self.controller.model_names)
        states = {}
        for name in names:
            try:
                states[name] = self.controller.state(name)
            except Exception:
                continue
        for name, state in states.items():
            self._show_lost(name, lost_devices(state))
        stop = self._stop_state()
        words = stop_words(stop)
        if words != self._words:
            self._words = words
            self.latched_label.setText(words["rail"])
            self.latched_row.setVisible(bool(words["rail"]))
            self.headline.setText(words["headline"])
            self.subline.setText(words["subline"])
            self.subline.setVisible(bool(words["subline"]))
            self.headline_row.setVisible(bool(words["headline"]))
        latched, unconfirmed = set(stop["latched"]), set(stop["unconfirmed"])
        for name, item in self._rail_items.items():
            item.set_stop("unconfirmed" if name in unconfirmed
                          else "stopped" if name in latched else None)
        for name, entry in self._entries.items():
            # The model's own word (L1): its stop did not confirm while latched.
            entry.set_unconfirmed((states.get(name) or {}).get("stop_confirmed") is False)
        if not latched:
            self._drop_unconfirmed_alerts()
        self.rail_status.set_full_text(self._rail_status_text(names, states))

    def _stop_state(self):
        """`Controller.stop_state`: what is latched, what did not confirm,
        and whether that is every model (L1)."""
        return self.controller.stop_state

    def _show_lost(self, name, lost):
        """A lost device, said on the rail and in the entry's own head."""
        if self._lost.get(name, []) == lost:
            return False
        self._lost[name] = lost
        entry = self._entries.get(name)
        if entry is not None:
            entry.set_lost(sentence(f"{' and '.join(lost)} lost") if lost else "")
        return True

    def _build_reopen(self, closed):
        """A model the operator closed is put away, not gone: the way back is
        in the rail, under the open ones."""
        while self._reopen_layout.count():
            item = self._reopen_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.reopen_buttons = {}
        if closed:
            self._reopen_layout.addWidget(_caption("Closed"))
        for name in closed:
            button = QPushButton(f"Reopen {name}")
            button.setObjectName("railModel")
            button.setToolTip(f"Reopen {name}")
            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            button.clicked.connect(lambda _=False, n=name: self._reopen(n))
            self._reopen_layout.addWidget(button)
            self.reopen_buttons[name] = button
        self._reopen_holder.setVisible(bool(closed))

    def _reopen(self, name):
        """Construct the model again from its remembered config, and bring its
        entry forward when it arrives (AUD-13). A refusal is logged and the
        button stays; it never raises out of a click."""
        if name in self._entries:
            self.open_entry(name)
            return
        self._raise_on_add = name
        try:
            self.open_model(name)
        except Exception as exc:
            self._raise_on_add = None
            events.warn("Reopen Failed", f"{name}: {exc}", source="QtView",
                        exception=exc)
        if name in self._entries:
            self._bring_forward(self._entries[name])
        self._sync_rail()

    def _bring_forward(self, entry):
        """Scroll the sheet to an entry and hand it focus."""
        self.sheet_scroll.ensureWidgetVisible(entry, 0, 0)
        entry.setFocus(Qt.FocusReason.OtherFocusReason)

    @property
    def page(self):
        """The shown page: `OVERVIEW`, or the name of the model shown alone."""
        return OVERVIEW if self._shown is None else self._shown

    def open_entry(self, name):
        """A model's rail item, or its overview head: the device page, that
        model alone and full width, its readings `focal`, its disclosures at
        the foot of its body (K4)."""
        if name not in self._entries:
            return
        entry = self._entries[name]
        from_keyboard = entry.head.hasFocus()
        self._shown = name
        self._arrange_entries()
        self._sync_rail()
        # The head that was pressed is inert on the device page; the
        # keyboard's place moves to the rail item that now names the page.
        if from_keyboard and name in self._rail_items:
            self._rail_items[name].setFocus(Qt.FocusReason.OtherFocusReason)
        self.sheet_scroll.verticalScrollBar().setValue(0)

    def show_overview(self):
        """The rail's "Overview": every model's compact entry, tier 1 only;
        the device that was shown is scrolled back into view."""
        was = self._shown
        self._shown = None
        self._arrange_entries()
        self._sync_rail()
        if was in self._entries:
            QTimer.singleShot(0, lambda: (
                self.sheet_scroll.ensureWidgetVisible(self._entries[was], 0, 0)
                if was in self._entries else None))

    # -- Setup: put away on launch, back from the rail ----------------------
    def _collapse_setup(self):
        """First model launched: the wizard gives way, one click from coming
        back. Hidden, never destroyed — the panel behind it keeps its scan
        results and its selections."""
        if self._setup_dock is None or self._setup_dock.isHidden():
            return
        self._setup_dock.hide()
        events.debug("Setup Collapsed", "the Setup dock is hidden; the "
                     "rail's Setup brings it back", source="QtView")

    def show_setup(self):
        """Bring Setup back for a re-scan or a relaunch (so does the rail)."""
        if self._setup_dock is None:
            return
        self._setup_dock.show()
        self._setup_dock.raise_()
        events.debug("Setup Reopened", "the Setup dock is visible again",
                     source="QtView")

    # -- the sheet -------------------------------------------------------------
    def _build_sheet(self):
        """The central widget: the sheet (one scroll for the page), the alert
        band and the tray under it."""
        page = QWidget()
        page.setObjectName("bare")
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        self.sheet_scroll = QScrollArea()
        self.sheet_scroll.setObjectName("sheetScroll")
        self.sheet_scroll.setWidgetResizable(True)
        self.sheet_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.sheet = QWidget()
        self.sheet.setObjectName("sheet")
        body = QVBoxLayout(self.sheet)
        side = theme.SPACE[9]
        body.setContentsMargins(side, theme.SPACE[8], side, theme.SPACE[9])
        body.setSpacing(theme.SPACE[8])
        # The stop's headline and, under it, its subline: `stop_words` (L1).
        # "Every model is stopped." only when every model is latched and
        # confirmed; a model that did not confirm is named instead.
        self.headline = QLabel("")
        self.headline.setObjectName("headline")
        self.headline.setWordWrap(True)
        self.subline = QLabel("")
        self.subline.setObjectName("headlineSub")
        self.subline.setWordWrap(True)
        self.headline_row = QWidget()
        self.headline_row.setObjectName("bare")
        line = QVBoxLayout(self.headline_row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.SPACE[1])
        line.addWidget(self.headline)
        line.addWidget(self.subline)
        self.headline_row.setVisible(False)
        body.addWidget(self.headline_row)
        body.addWidget(self._build_empty_state())
        # Rows of entries, one box per row: a QGridLayout with spans drops the
        # height-for-width of a spanning entry, and an opened well was cut.
        self._rows_holder = QWidget()
        self._rows_holder.setObjectName("bare")
        self._rows = QVBoxLayout(self._rows_holder)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(theme.SPACE[9])
        body.addWidget(self._rows_holder)
        body.addStretch(1)
        self.sheet_scroll.setWidget(self.sheet)
        self.sheet_scroll.viewport().installEventFilter(self)
        column.addWidget(self.sheet_scroll, 1)
        column.addWidget(self._build_alert_band())
        column.addWidget(self._build_event_tray())
        self.setCentralWidget(page)

    def eventFilter(self, watched, event):     # noqa: N802 - Qt's name
        scroll = getattr(self, "sheet_scroll", None)
        if (scroll is not None and event.type() == QEvent.Type.Resize
                and watched is scroll.viewport()):
            self._schedule_arrange()
        return QMainWindow.eventFilter(self, watched, event)

    def _sheet_room(self):
        margins = self.sheet.layout().contentsMargins()
        return self.sheet_scroll.viewport().width() - margins.left() - margins.right()

    def _fit_columns(self, names):
        """As many columns as fit with no entry under its minimum - never a
        clipped number (F5) - up to three."""
        room = self._sheet_room()
        gutter = SHEET_GUTTER
        for columns in range(MAX_SHEET_COLUMNS, 1, -1):
            rows = entry_rows(names, None, columns)
            if all(max(self._entries[n].minimumSizeHint().width() for n in row)
                   * len(row) + gutter * (len(row) - 1) <= room
                   for row in rows):
                return columns
        return 1

    def _arrange_entries(self):
        """The shown page (K4). The overview: every entry compact, in rows of
        three and two, as many columns as fit (`entry_rows`). A device page:
        that entry alone, full width; the others are hidden, so their panels
        stop ticking (F21) and draw nothing."""
        order = [n for n in self.controller.model_names if n in self._entries]
        names = order + [n for n in self._entries if n not in order]
        if self._shown is not None and self._shown not in names:
            # The shown device closed, or never came back: the overview.
            self._shown = None
            self._sync_rail()
        if not names:
            self._arrangement = None
            return
        overview = self._shown is None
        for name, entry in self._entries.items():
            # Only the shown device is drawn for its page; every other entry
            # stays compact (hidden on a device page, and ready for the grid).
            compact = name != self._shown
            if entry.is_overview != compact:
                entry.set_page(compact)
        for entry in self._entries.values():
            # Columns share the width equally; each entry keeps its own floor.
            entry.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            least = entry.minimumSizeHint().width()
            if entry.minimumWidth() != least:
                entry.setMinimumWidth(least)
        rows = (entry_rows(names, None, self._fit_columns(names)) if overview
                else [[self._shown]])
        arrangement = tuple(tuple(row) for row in rows)
        if arrangement == self._arrangement:
            return
        self._arrangement = arrangement
        shown = {n for row in rows for n in row}
        for name, entry in self._entries.items():
            if entry.isHidden() == (name in shown):
                entry.setVisible(name in shown)
        while self._rows.count():
            line = self._rows.takeAt(0).layout()
            while line is not None and line.count():
                line.takeAt(0)
            if line is not None:
                line.deleteLater()
        for row in rows:
            line = QHBoxLayout()
            line.setSpacing(SHEET_GUTTER)
            for name in row:
                line.addWidget(self._entries[name], 1)
            self._rows.addLayout(line)
        events.debug("Sheet Arranged", " | ".join(", ".join(r) for r in rows),
                     source="QtView")

    def _schedule_arrange(self):
        """Arrange once the layout has settled (sizes read before it are the
        old ones)."""
        if self._arrange_pending:
            return
        self._arrange_pending = True

        def run():
            self._arrange_pending = False
            if not self._closing:
                self._arrange_entries()
                self._sync_setup_height()
                self._fit_well()
        QTimer.singleShot(0, run)

    def _fit_well(self):
        """L5 (QT7-15): on the device page the head and tier 1 never scroll
        away. The well takes the room left under them on the page and
        scrolls inside it; a well shorter than that room is its own height.
        (At a launch font so large that tier 1 alone overflows, the well
        keeps a few lines and the page scrolls, rather than hiding it.)"""
        panel = self._panels.get(self._shown) if self._shown is not None else None
        scroll = getattr(panel, "well_scroll", None)
        if scroll is None or scroll.isHidden() or not panel.tier_is_shown(2):
            return
        content = panel.well_content_height()
        sheet = self.sheet
        page = (sheet.heightForWidth(sheet.width()) if sheet.hasHeightForWidth()
                else sheet.sizeHint().height())
        others = max(0, page - scroll.height())
        room = self.sheet_scroll.viewport().height() - others
        floor = StopButton.line_height() * 4
        height = max(min(content, room), min(content, floor))
        if scroll.minimumHeight() != height or scroll.maximumHeight() != height:
            scroll.setFixedHeight(height)
            # A parent layout caches its children's sizes; a fixed height set
            # deep inside is not news to it until each level is told.
            widget = scroll.parentWidget()
            while widget is not None and widget is not self.sheet_scroll:
                widget.updateGeometry()
                widget = widget.parentWidget()
            self._schedule_arrange()

    def showEvent(self, event):
        """A resize made while the window was hidden is delivered only when it
        shows; the rail and the disc follow it then too."""
        super().showEvent(event)
        self._sync_rail_width()
        self._schedule_arrange()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "sheet_scroll"):
            self._sync_rail_width()
            self._sync_setup_height()
            self._schedule_arrange()

    # -- the empty state ---------------------------------------------------
    def _build_empty_state(self):
        """What the sheet says when nothing is running: what to do next."""
        self.empty_state = QWidget()
        self.empty_state.setObjectName("bare")
        layout = QVBoxLayout(self.empty_state)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.GAP)
        title = QLabel(EMPTY_TITLE)
        title.setObjectName("emptyTitle")
        hint = QLabel(EMPTY_HINT)
        hint.setObjectName("caption")
        hint.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(hint)
        self.empty_setup_button = QPushButton("Open setup")
        self.empty_setup_button.setObjectName("ghost")
        self.empty_setup_button.clicked.connect(self.show_setup)
        self.empty_setup_button.setVisible(False)
        layout.addSpacing(theme.PAD)
        layout.addWidget(self.empty_setup_button, 0, Qt.AlignmentFlag.AlignLeft)
        return self.empty_state

    def _sync_empty_state(self):
        """Shown while no model is open; the way to Setup when it is away."""
        self.empty_state.setVisible(not self._entries)
        setup_away = self._setup_dock is not None and self._setup_dock.isHidden()
        self.empty_setup_button.setVisible(setup_away)

    # -- events: the tray --------------------------------------------------
    def _build_event_tray(self):
        """One line under the sheet: the latest warning or error, the rest a
        click away. Normal is silence (the brief): information and debug lines
        go to the log file, not here."""
        self.tray = QFrame()
        self.tray.setObjectName("tray")
        layout = QVBoxLayout(self.tray)
        side = theme.SPACE[9]
        layout.setContentsMargins(side, theme.PAD, side, theme.PAD)
        layout.setSpacing(theme.GAP)
        head = QHBoxLayout()
        head.setSpacing(theme.PAD)
        self._event_mark_style = mark_sheet(theme.SEVERITY_MARK["error"])
        self.event_mark = mark(theme.SEVERITY_MARK["error"])
        self.event_mark.setVisible(False)
        head.addWidget(self.event_mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self.event_latest = ElidedLabel()
        self.event_latest.setObjectName("trayLatest")
        self.event_latest.setProperty("severity", "info")
        self.event_latest.set_full_text("")
        # Hidden while the log is open, but its room kept: the toggle stays
        # where it was pressed (QT7-9).
        policy = self.event_latest.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.event_latest.setSizePolicy(policy)
        head.addWidget(self.event_latest, 1)
        self.tray_toggle = QPushButton("Show events")
        self.tray_toggle.setObjectName("ghost")
        self.tray_toggle.setCheckable(True)
        self.tray_toggle.toggled.connect(self._set_tray_open)
        head.addWidget(self.tray_toggle)
        layout.addLayout(head)
        self.event_view = QTextEdit()
        self.event_view.setReadOnly(True)
        self.event_view.document().setMaximumBlockCount(EVENT_LOG_LINES)
        self.event_view.setFixedHeight(EVENT_LOG_PX)
        layout.addWidget(self.event_view)
        return self.tray

    def _set_tray_open(self, is_open):
        is_open = bool(is_open)
        self.event_view.setVisible(is_open)
        self.tray_toggle.setText("Hide events" if is_open else "Show events")
        if self.tray_toggle.isChecked() != is_open:
            self.tray_toggle.blockSignals(True)
            self.tray_toggle.setChecked(is_open)
            self.tray_toggle.blockSignals(False)
        has_line = bool(self.event_latest.full_text())
        self.event_latest.setVisible(not is_open)
        self.event_mark.setVisible(not is_open and has_line)
        if is_open:
            self.event_view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _severity_colour(severity):
        """The ink an event's text is drawn in: legible first (F14). The
        severity's colour goes in its mark, beside the word."""
        return theme.SEVERITY_INK.get(severity, theme.MUTED)

    @staticmethod
    def _severity_mark(severity):
        return theme.SEVERITY_MARK.get(severity, theme.MUTED)

    def _mark_resource(self, severity, colour):
        """The hollow square for `severity`, registered once in the log's
        document as `mark:<severity>`: a 2 px `colour` edge, clear inside."""
        name = f"mark:{severity}"
        document = self.event_view.document()
        if document.resource(QTextDocument.ResourceType.ImageResource.value,
                             QUrl(name)) is None:
            side = self.event_view.fontMetrics().ascent()
            image = QImage(side, side, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            pen = QPen(QColor(colour))
            pen.setWidth(2)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.drawRect(QRect(1, 1, side - 2, side - 2))
            painter.end()
            document.addResource(QTextDocument.ResourceType.ImageResource.value,
                                 QUrl(name), image)
        return name

    def _hang_last_line(self, hollow):
        """A wrapped line runs under its words, not under its mark (QT7-19):
        a hanging indent the width of the mark and its gap."""
        metrics = self.event_view.fontMetrics()
        gap = metrics.horizontalAdvance("\u00a0\u00a0")
        mark_px = metrics.ascent() if hollow else gap
        indent = float(mark_px + gap)
        cursor = QTextCursor(self.event_view.document().lastBlock())
        fmt = cursor.blockFormat()
        fmt.setLeftMargin(indent)
        fmt.setTextIndent(-indent)
        cursor.setBlockFormat(fmt)

    #: What the tray reports: warnings and errors only (status by exception).
    TRAY_SEVERITIES = ("warning", "error")

    def _show_event(self, event):
        """A warning or an error, once: a mark (solid signal for an error, a
        hollow ink square for a warning), the severity as a word, the line in
        a legible ink - never colour alone (F14, AUD-5)."""
        severity = str(event.severity)
        if severity not in self.TRAY_SEVERITIES:
            return
        text = event_line(event)
        key = (severity, text)
        if key == self._last_event:
            return                      # one event, once
        self._last_event = key
        ink = self._severity_colour(severity)
        label = sentence(severity.upper())
        hollow = severity in theme.SEVERITY_MARK_HOLLOW
        colour = self._severity_mark(severity)
        # A warning's mark is a hollow ink square, an error's a solid signal
        # one (the brief). Rich text drops a span's border, so the hollow
        # square is a small image drawn from the theme and kept as a document
        # resource; the solid one is a filled cell.
        cell = (f'<img src="{self._mark_resource(severity, colour)}">' if hollow
                else f'<span style="background-color:{colour}">&nbsp;&nbsp;</span>')
        # Escaped: an event's text can carry a device's reply verbatim, and
        # this widget renders HTML.
        self.event_view.append(
            f'{cell}&nbsp;&nbsp;'
            f'<span style="color:{ink}"><b>{html.escape(label)}</b>&nbsp;&nbsp;'
            f"{html.escape(text)}</span>")
        self._hang_last_line(hollow)
        self.event_view.moveCursor(QTextCursor.MoveOperation.End)
        self.event_latest.set_full_text(f"{label}  {text}")
        self._latest_title = getattr(event, "title", None)
        if self.event_latest.property("severity") != severity:
            self.event_latest.setProperty("severity", severity)
            self.event_latest.style().unpolish(self.event_latest)
            self.event_latest.style().polish(self.event_latest)
        style = mark_sheet(colour, hollow)
        if style != self._event_mark_style:
            self._event_mark_style = style
            self.event_mark.setStyleSheet(style)
        self.event_mark.setVisible(not self.tray_toggle.isChecked())

    def _build_alert_band(self):
        """One line per unacknowledged error, oldest first: a fault mark, the
        word, the message, how many are waiting, and the acknowledgement. The
        errors queue; the next one never overwrites the last (HC-2). Under
        the sheet, never over the rail's stop (F1)."""
        self.alert_band = QFrame()
        self.alert_band.setObjectName("alertBand")
        self.alert_band.setAccessibleName("Errors waiting to be acknowledged")
        row = QHBoxLayout(self.alert_band)
        side = theme.SPACE[9]
        row.setContentsMargins(side, theme.PAD, side, theme.PAD)
        row.setSpacing(theme.PAD)
        row.addWidget(mark(theme.SEVERITY_MARK["error"]), 0, Qt.AlignmentFlag.AlignVCenter)
        self.alert_word = QLabel("Error")
        self.alert_word.setObjectName("alertWord")
        row.addWidget(self.alert_word)
        self.alert_text = ElidedLabel()
        self.alert_text.setObjectName("alertText")
        row.addWidget(self.alert_text, 1)
        self.alert_count = QLabel("")
        self.alert_count.setObjectName("caption")
        row.addWidget(self.alert_count)
        self.alert_ack = QPushButton("Acknowledge")
        self.alert_ack.setObjectName("ghost")
        self.alert_ack.clicked.connect(self.acknowledge)
        row.addWidget(self.alert_ack)
        self.alert_ack_all = QPushButton("Acknowledge all")
        self.alert_ack_all.setObjectName("ghost")
        self.alert_ack_all.clicked.connect(self.acknowledge_all)
        row.addWidget(self.alert_ack_all)
        self.alert_band.setVisible(False)
        return self.alert_band

    def _show_popup(self, event):
        """An error that wants acknowledging joins the alert band's queue -
        never a blocking modal over the stop (F1, HC-2, UXPM-1)."""
        if self._closing:
            return
        events.debug("Alert Queued", f"{event.severity}: {event.text}",
                     source="QtView")
        # One event, once: the band carries it, so the tray's line does not
        # say it a second time (it stays in the full log).
        if self.event_latest.full_text().endswith(event_line(event)):
            self.event_latest.set_full_text("")
            self.event_mark.setVisible(False)
        self._alerts.append(event)
        self._render_alerts()

    def _render_alerts(self):
        if not self._alerts:
            self.alert_band.setVisible(False)
            return
        first = self._alerts[0]
        self.alert_word.setText(sentence(str(first.severity).capitalize()))
        self.alert_text.set_full_text(self._popup_text(first))
        waiting = len(self._alerts)
        self.alert_count.setText(f"1 of {waiting}" if waiting > 1 else "")
        self.alert_count.setVisible(waiting > 1)
        self.alert_ack_all.setVisible(waiting > 1)
        self.alert_band.setVisible(True)

    def acknowledge(self):
        """The oldest waiting error is read; the next one takes its place."""
        if self._alerts:
            self._alerts.pop(0)
        self._render_alerts()

    def acknowledge_all(self):
        self._alerts.clear()
        self._render_alerts()

    @property
    def alerts(self):
        return list(self._alerts)

    def _drop_unconfirmed_alerts(self):
        """L2: once the latch opens, "did not confirm" is history, not an
        alert: its line leaves the band (and the tray's latest line). While
        latched it waits for its acknowledgement like any error."""
        kept = [a for a in self._alerts
                if getattr(a, "title", None) != UNCONFIRMED_TITLE]
        if len(kept) != len(self._alerts):
            self._alerts = kept
            self._render_alerts()
        if self._latest_title == UNCONFIRMED_TITLE:
            self._latest_title = None
            self.event_latest.set_full_text("")
            self.event_mark.setVisible(False)

    @staticmethod
    def _popup_text(event):
        """The whole line - title and message, in the operator's words
        (HC-11, L11)."""
        return event_line(event)[:5000]

    def _confirm(self, prompt):
        return ask(self, prompt)

    # -- panels ------------------------------------------------------------
    def _add_panel(self, name):
        if name in self._entries:
            return self._entries[name]
        panel = QtPanelView(self.controller, name)
        entry = SheetEntry(name, panel, self.sheet)
        entry.closed.connect(lambda: self._on_entry_closed(name))
        entry.open_requested.connect(lambda: self.open_entry(name))
        entry.set_page(True)
        self._entries[name] = entry
        self._panels[name] = panel
        if self._lost.get(name):
            entry.set_lost(sentence(f"{' and '.join(self._lost[name])} lost"))
        reopened = self._raise_on_add == name
        # A model Setup launched lands on the overview (K4); one reopened
        # from the rail is shown alone, like a press on its rail item.
        self._shown = name if reopened else None
        self._arrangement = None
        self._sync_empty_state()
        self._arrange_entries()
        self._schedule_arrange()
        self._sync_rail()
        if reopened:
            self._raise_on_add = None
            self._bring_forward(entry)
        events.debug("Entry Opened", name, source="QtView")
        return entry

    def _remove_panel(self, name):
        entry = self._entries.pop(name, None)   # popped first: the entry's own
        panel = self._panels.pop(name, None)    # closeEvent must not re-enter
        self._lost.pop(name, None)
        if entry is None:
            self._sync_rail()
            return
        if panel is not None:
            panel.close()
        entry.close()
        entry.setParent(None)
        entry.deleteLater()
        self._arrangement = None
        self._sync_empty_state()
        self._arrange_entries()
        self._sync_rail()
        events.debug("Entry Closed", name, source="QtView")

    def _on_entry_closed(self, name):
        """The entry's close. Closing an entry closes the model (no hide)."""
        if self._closing or name not in self._entries:
            return
        self.close_model(name)

    # -- the global stop ---------------------------------------------------
    def toggle_estop_all(self):
        """The disc's press, decided by `stop_words` (L1): a clear only while
        EVERY model is latched; otherwise a stop of every model, so one
        model's own switch never takes the stop away from the rest. The clear
        asks first, titled, answered by verbs (L14)."""
        words = stop_words(self._stop_state())
        if words["action"] != "clear":
            return self.controller.estop_all()
        result = self.controller.clear_estop_all()
        if result.needs_confirm and ask(self, result.reason, *CLEAR_WORDS):
            result = self.controller.clear_estop_all(confirmed=True)
        return result

    def _on_stop_clicked(self):
        # An open question is answered No first: it never stands between the
        # operator and the stop, and never says Yes for them afterwards.
        cancel_pending_confirms()
        self.toggle_estop_all()
        self._sync_stop_button()
        self._sync_states()

    def _on_stop_shortcut(self):
        """The keyboard's stop: it stops, and does nothing once every model
        is stopped - clearing the latch is a deliberate press of the face
        (F9). A partial stop is no reason to refuse: the rest still run."""
        if stop_words(self._stop_state())["action"] == "clear":
            return
        cancel_pending_confirms()
        self.controller.estop_all()
        self._sync_stop_button()
        self._sync_states()

    @staticmethod
    def stop_shortcut_text():
        """The chord's name, spelled out: Qt's native text would print a
        glyph on macOS, and the chord reads the same on every OS."""
        return STOP_SHORTCUT_TEXT

    def _sync_stop_button(self):
        """Face and ring from the Controller's `stop_state`, never from the
        last click: the face is `stop_words`' (L1)."""
        words = stop_words(self._stop_state())
        clears = words["action"] == "clear"
        if self.stop_button.is_latched != clears:
            events.debug("Stop Button", f"face={words['face']}", source="QtView")
        self.stop_button.set_latched(clears, words["face"])
        name = CLEAR_HINT if clears else STOP_HINT
        tip = (f"{name}." if clears else
               f"{name} ({self.stop_shortcut_text()}). Space or Return presses "
               f"it when it has focus.")
        if self.stop_button.toolTip() != tip:
            self.stop_button.setToolTip(tip)
            self.stop_button.setAccessibleName(name)

    # -- focus (D-4) -------------------------------------------------------
    def changeEvent(self, event):
        if event.type() in (QEvent.Type.WindowActivate,
                            QEvent.Type.WindowDeactivate):
            # Deferred one turn on purpose: on deactivate Qt has not yet made
            # the new window active, so asking now cannot tell "the operator
            # switched apps" from "this app opened a dialog".
            QTimer.singleShot(0, self._sync_focus_gate)
        super().changeEvent(event)

    def _app_has_focus(self):
        """True while any window of *this* application is active.

        A child dialog - a file picker, a confirmation, an error box - keeps
        the application focused although the main window is deactivated.
        Treating that as focus loss is the defect behind PYSIDE-14, and D-4
        says focus gates input; it never stops anything.
        """
        application = QApplication.instance()
        return bool(application and application.activeWindow() is not None)

    def _sync_focus_gate(self):
        is_focused = self._app_has_focus()
        if is_focused == self._is_focused:
            return
        self._is_focused = is_focused
        events.debug("Focus Gate", f"input gate {'open' if is_focused else 'closed'}",
                     source="QtView")
        self._on_focus_change(is_focused)
