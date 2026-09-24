import sys
"""The PySide6 frontend: one window, one panel renderer, three pure widgets.

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

Layout
------
`schema.section(..., layout=)` is a hint every renderer has to honour.
`_make_section` returns one of two containers with a single `add`/`add_wide`
API — `ColumnSection` (a form, one labelled control per line) or a `TableRow`
in the panel's shared `PanelTable` — so the thirteen element builders below
carry no layout branch at all. A row section's columns are allocated by
*label*, which is what lines "Port" up under "Port" across models that do not
all declare the same controls. Setup is the panel this exists for: one line
per model type instead of six stacked forms (Addendum 2). A row section that
runs something (`is_action_row`: Setup's Devices and Launch lines) spans the
table instead of inventing columns.

The window is the Web view's sibling: a rail across the top carrying each
open model's key numbers and the stop object, the model docks under it (their
own titles are the only navigation), Setup as a dock that gives way on
launch, and the event log as a one-line tray in the status bar. Copy is
sentence case (`sentence_case`, the Web view's rule); signal red is spent on
the stop object alone.

Styling
-------
Only through `views.theme`. `stylesheet()` generates the whole sheet
from theme values, which is why there is no `.qss` file, no colour literal
and no pixel font size anywhere below. The sheet goes on the QApplication,
not the window, so message boxes and file dialogs are themed too (PYSIDE-15).

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

import schema as sch
from events import events
from views import theme
from views.base import Dashboard, PanelView

try:                                    # the module imports without PySide6
    from PySide6.QtCore import (QEasingCurve, QEvent, QEventLoop, QLocale,
                                QPoint, QRect, QRectF, QSize, Qt, QTimer,
                                QVariantAnimation, Signal)
    from PySide6.QtGui import (QColor, QDoubleValidator, QFont, QFontMetrics,
                               QIcon, QIntValidator, QKeySequence, QPainter,
                               QPen, QPixmap, QShortcut, QTextCursor)
    from PySide6.QtWidgets import (
        QAbstractButton, QApplication, QComboBox, QDockWidget, QFileDialog,
        QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
        QMainWindow, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
        QStatusBar, QStyle, QStyleOptionComboBox, QStylePainter, QTextEdit,
        QToolButton, QVBoxLayout, QWidget)
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
    QComboBox = QFrame = _NoQt


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

# -- geometry. Padding and gaps come from `theme.PAD / GAP / INSET`; what is
# left here is the handful of sizes the theme has no opinion about, and those
# are measured from the font wherever the thing they size holds text.
#: Every table column that holds a control, so a row with no gamepad still
#: lines up with one that has one.
TABLE_CONTROL_MIN_PX = 150
#: More cards than this and a panel is laid out in two columns. Stacked in
#: one, Red Percent's ten sections made a dock twice the screen's height and
#: put Safety - the section that must stay reachable - below the fold.
COLUMN_SPLIT_CARDS = 6
#: A dropdown sizes to this many characters rather than to its longest option,
#: which is what makes a column of them one width instead of four. A longer
#: name is elided from the middle - "/dev/cu.usbm…4401" - because the end of
#: a port or gamepad name is the part that tells two devices apart (F15).
DROPDOWN_CHARS = 14
#: An `indicator` is a lamp: a dot beside its label, never a full-width box.
#: It used to be a `valueLabel` carrying the element's own text, so every
#: indicator on the bench read "Fault    Fault". This is its floor; above
#: 12 pt it follows the font (`lamp_px`), so it is not a speck beside 28 pt
#: text (AUD-12).
LAMP_PX = 16
#: A `log_stream` is a few scrollable lines, fixed. Left to expand, the
#: stepper's Gamepad Log took a third of the panel and pushed Safety - the
#: section that has to be reachable - off the bottom of the dock.
LOG_STREAM_PX = 110
#: The event tray's open height, and the number of lines it keeps. Closed, it
#: is one line: the latest event. It is a companion to the panels, not the
#: main event, and an append-forever log is an unbounded document in a window
#: meant to run for a whole bench session.
EVENT_LOG_PX, EVENT_LOG_LINES = 150, 500
#: The stop object's disc, in lines of the base font, so it follows
#: --font-size: the rail's stop, and one size down, a model's own stop in its
#: Safety section. The same object at two sizes, as in the Web view.
STOP_DISC_LINES, MINI_STOP_LINES = 4.6, 2.9
#: ...up to this. At 28 pt the disc was 170 px and the rail plus the tray took
#: 246 of a 700 px window (AUD-8); the face shrinks to fit instead.
STOP_MAX_PX = 96
#: The type scale is `theme.size(step)`: -1 a caption, 0 the base, 1 a
#: readout or a heading, 2 a panel name. The stop's face is one step up.
STEP_CAPTION, STEP_BASE, STEP_READOUT, STEP_TITLE = -1, 0, 1, 2
STOP_FACE_STEP = STEP_READOUT
#: The pulse when the latch closes: once, on the edge, never while it stays.
#: `STATION_NO_MOTION=1` turns it off (`motion_reduced`); the face and the
#: ring still change, so the state still shows (AUD-14).
PULSE_MS = 400
DANGER_ROLE = "danger"
#: The global stop's keyboard shortcut: Cmd+. on macOS (the platform's own
#: "stop what you are doing"), Ctrl+. elsewhere. It only ever stops; clearing
#: stays a deliberate press of the face, and a question (F9).
STOP_SHORTCUT = "Ctrl+."
#: Qt's "Ctrl" is the Command key on macOS, so there the physical
#: Control-period is bound as well, as Tk binds both (`Meta` is Control on
#: macOS in Qt's naming).
STOP_SHORTCUTS = (STOP_SHORTCUT,) + (("Meta+.",) if sys.platform == "darwin"
                                     else ())
#: The stop's two names, the Web and Tk views' words: what a press does.
STOP_HINT, CLEAR_HINT = "Stop every model", "Clear the stop on every model"
#: How many of a model's key numbers the rail carries (the Web view's
#: RAIL_READOUTS). More and it stops being readable at a glance.
RAIL_READOUTS = 4
#: The rail's readout groups wrap onto at most this many lines; past that a
#: model shows fewer readouts rather than a clipped one (F5).
RAIL_LINES = 2
#: A rail caption elides to no fewer than this many characters before its
#: group wraps; the number beside it is never elided at all.
RAIL_CAPTION_CHARS = 4
#: A panel readout wider than this many characters is elided from the middle
#: with the whole value as its tooltip, so a 60-character Run ID cannot push
#: the column sideways (F15). A number never comes near it.
READOUT_MAX_CHARS = 28
#: Model docks side by side; the fourth and later are tabs in the last one,
#: so no dock is squeezed to a 100 px sliver (F22, AUD-7).
MAX_DOCK_COLUMNS = 3
#: What a dock column is offered when the columns are shared out: at least
#: this, at most twice it, in proportion to what its panel asks for.
MIN_DOCK_PX = 360
#: The resting border of a control's well: 3.1:1 on the card, where the
#: 12 %/24 % rules are a hairline and a separator (1.4:1, 2.0:1) and too faint
#: to find a field by (WCAG 1.4.11; AUD-11). Derived with the theme's one
#: `mix`; CORE CHANGE REQUEST: move it to theme.py as INPUT_BORDER.
INPUT_BORDER = theme.mix(theme.SURFACE, theme.TEXT, 0.40)
#: Keyboard focus: two pixels of ink on every control (F25). Never trace -
#: a trace ring is what the latched stop looks like.
FOCUS_RING = f"2px solid {theme.STOP_FOCUS}"
#: A readout whose value is one of these is at rest, not live: it is drawn in
#: the muted ink, so "off" never competes with a number that is moving.
QUIET_VALUES = frozenset({"", "off", "none", "false", "no", "--",
                          "nothing selected", "not scanned", "not scanned yet"})
#: What an empty readout shows: a dash, not a blank that reads as broken.
EMPTY_READOUT = "\u2014"
#: The empty state: what to do next, not "nothing here".
EMPTY_TITLE = "No instruments running."
EMPTY_HINT = "Choose ports in Setup and press Launch."


# ---------------------------------------------------------------------------
# Pure functions. No Qt, no widgets, unit-tested without a QApplication.
# ---------------------------------------------------------------------------

def _rule(selector, declarations):
    body = "".join(f"    {name}: {value};\n" for name, value in declarations.items())
    return f"{selector} {{\n{body}}}\n"


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
    """What the rail and the panel say about a lost device."""
    return f"{name} lost its {' and '.join(lost)}"


def lamp_colours(element, is_on):
    """(fill, ring) of a lamp - Tk's rule, so the two views agree (AUD-3).

    Lit is trace, the brief's "indicator lamps ON", or signal when being on is
    a fault. Unlit is a muted ring on the card; unlit where *off* is the fault
    (a disconnected stage) is an ink ring - louder than muted, never a second
    red. The old lamp borrowed the toggle's role fills: a lit "Stage
    connected" was 1.50:1 against the card, an unlit Fault 1.23:1.
    """
    if is_on:
        lit = theme.SIGNAL if element.get("on_role") == DANGER_ROLE else theme.TRACE
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


def as_operator_word(text):
    """A boolean readout is shown as the operator would say it ("Yes" / "No"),
    not as a programming literal - the Web view's rule (`app.js`), so the two
    views read the same."""
    if isinstance(text, bool):
        return "Yes" if text else "No"
    if isinstance(text, str) and text.strip().lower() in ("true", "false"):
        return "Yes" if text.strip().lower() == "true" else "No"
    return text


def is_quiet_value(text):
    """A readout at rest ("off", "False", nothing) rather than a live one."""
    return str("" if text is None else text).strip().lower() in QUIET_VALUES


def rail_elements(schema):
    """The key numbers the rail carries for a model (the Web view's
    `railElements`): the readouts the model flags `rail=True`, else the
    readonly elements of its first section that has any. Derived, never named,
    so a model this file has never heard of still shows up on the rail."""
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


def stylesheet():
    """The whole Qt stylesheet, generated from `views.theme`.

    Every colour and every font size in this string comes from a theme value.
    That is the entire reason `style.qss` is deleted rather than ported: a
    checked-in sheet is a second palette that drifts from the other two views
    and cannot follow `--font-size` at launch.

    One scale (`theme.size`, ratio 1.2) and one family; spacing from
    `theme.SPACE` and the PAD/GAP/INSET names, never a sum. Every control has
    its hover, focus, pressed and disabled state here; focus is two pixels of
    ink everywhere (F25). Signal red is not in this sheet at all: the stop
    object and the fault marks dress themselves.
    """
    base_font, base_size, _ = theme.font()
    small_size = theme.size(STEP_CAPTION)
    heading_size = theme.size(STEP_READOUT)
    # Capped at one step up: two steps made a 28 pt rail that, with the
    # tray, took a third of the window (F25, AUD-8).
    readout_size = theme.size(STEP_READOUT)
    neutral_bg, neutral_fg = theme.colors("neutral")
    info_bg, _ = theme.colors("info")
    go_bg, _ = theme.colors("go")
    disabled_bg, disabled_fg = theme.DISABLED
    rule, rule_strong = theme.RULE, theme.RULE_STRONG
    hair, tight = theme.SPACE[0], theme.SPACE[1]
    ring = FOCUS_RING

    sheet = [
        _rule("QWidget", {"background-color": theme.BACKGROUND,
                          "color": theme.TEXT,
                          "font-family": base_font,
                          "font-size": f"{base_size}pt"}),
        _rule("QMainWindow, QDialog, QMessageBox",
              {"background-color": theme.BACKGROUND}),
        _rule("QMainWindow::separator", {"background": rule, "width": "1px",
                                         "height": "1px"}),
        # A label paints no rectangle of its own: without this every caption
        # inside a card stamped a BACKGROUND patch over the card's SURFACE.
        _rule("QLabel", {"background": "transparent"}),
        # Holders that only place other widgets paint nothing: left to the
        # QWidget rule they stamped a window-coloured block on a card or on
        # the rail.
        _rule("QWidget#bare, QWidget#tableBar, QWidget#railReadouts",
              {"background": "transparent"}),
        _rule("QFrame#card", {"background-color": theme.SURFACE,
                              "border": f"1px solid {rule}",
                              "border-radius": f"{theme.GAP}px"}),
        _rule("QLabel#sectionTitle", {"color": theme.TEXT,
                                      "font-weight": "600"}),
        _rule("QLabel#caption", {"color": theme.MUTED}),
        # The table's furniture: a column caption is quiet, a row's own name
        # is not, so a Setup row reads as "Stepper Probe: this port" rather
        # than as four equally loud words.
        _rule("QLabel#columnHeader", {"color": theme.MUTED,
                                      "font-size": f"{small_size}pt",
                                      "padding-bottom": f"{hair}px"}),
        _rule("QLabel#rowTitle", {"color": theme.TEXT, "font-weight": "600",
                                  "padding-right": f"{theme.INSET}px"}),
        # A readout is a number in the trace colour straight on the card; an
        # entry is a bordered well sunk to the window colour. Shape and hue
        # both, so the two never read as one another.
        _rule("QLabel#valueLabel", {"color": theme.TRACE, "font-weight": "500",
                                    "padding": f"{theme.GAP}px {hair}px"}),
        _rule('QLabel#valueLabel[quiet="true"]', {"color": theme.MUTED}),
        # A refusal sits in the card of the control that caused it (F10).
        # Ink, with the word "Refused" as its mark - not a live-reading trace
        # line at the foot of the panel.
        _rule("QLabel#refusal", {"color": theme.TEXT,
                                 "padding": f"{tight}px 0px"}),
        _rule("QLabel#staleLabel, QLabel#notice",
              {"color": theme.TEXT,
               "padding": f"{hair}px {theme.GAP}px"}),
        # `_set_stale` sets this property on the panel - stale, or a device
        # lost - and every readout dims at once, so a frozen value can never
        # read as a live one.
        _rule('QWidget[stale="true"] QLabel#valueLabel', {"color": theme.MUTED}),
        _rule("QLineEdit, QComboBox, QTextEdit",
              {"background-color": theme.WELL, "color": theme.TEXT,
               "border": f"1px solid {INPUT_BORDER}", "border-radius": "2px",
               "padding": f"{theme.GAP}px {theme.PAD}px",
               "selection-background-color": info_bg,
               "selection-color": theme.TEXT}),
        _rule("QLineEdit:hover, QComboBox:hover", {"border-color": theme.MUTED}),
        _rule("QLineEdit:focus, QComboBox:focus, QTextEdit:focus",
              {"border": ring}),
        _rule("QComboBox QAbstractItemView",
              {"background-color": theme.SURFACE, "color": theme.TEXT,
               "border": f"1px solid {rule_strong}",
               "selection-background-color": go_bg}),
        _rule("QPushButton", {"background-color": neutral_bg,
                              "color": neutral_fg,
                              "border": f"1px solid {INPUT_BORDER}",
                              "border-radius": "2px",
                              "padding": f"{theme.GAP}px {theme.INSET}px",
                              "font-weight": "500"}),
        _rule("QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled",
              {"background-color": disabled_bg, "color": disabled_fg,
               "border-color": rule}),
    ]
    for role, (background, foreground) in theme.ROLES.items():
        if role == DANGER_ROLE:
            # One red. A button that merely *says* stop (Red Percent's "Stop"
            # run) is an ordinary command; signal red is spent on the stop
            # object alone, which dresses itself below the sheet.
            background, foreground = neutral_bg, neutral_fg
        sheet.append(_rule(f'QPushButton[role="{role}"]',
                           {"background-color": background,
                            "color": foreground}))
    # States after the roles, so they win at equal specificity.
    sheet += [
        _rule("QPushButton:hover", {"border-color": theme.MUTED,
                                    "background-color": theme.LIFT}),
        _rule("QPushButton:focus", {"border": ring}),
        _rule("QPushButton:pressed", {"background-color": theme.BACKGROUND}),
        _rule("QPushButton:disabled", {"background-color": disabled_bg,
                                       "color": disabled_fg,
                                       "border-color": rule}),
        # Chrome, not instrument controls: the rail's Setup and Events, the
        # tray's toggle, a closed model waiting to be reopened, an alert's
        # acknowledgement, a dock's close.
        _rule("QPushButton#ghost, QToolButton#ghost",
              {"background": "transparent", "color": theme.MUTED,
               "border": f"1px solid {rule_strong}", "border-radius": "2px",
               "padding": f"{theme.GAP}px {theme.PAD}px",
               "font-weight": "400"}),
        _rule("QPushButton#ghost:hover, QToolButton#ghost:hover",
              {"color": theme.TEXT, "border-color": theme.MUTED}),
        _rule("QPushButton#ghost:checked, QToolButton#ghost:checked",
              {"color": theme.TEXT, "background": theme.SURFACE,
               "border-color": theme.MUTED}),
        _rule("QPushButton#ghost:focus, QToolButton#ghost:focus",
              {"border": ring}),
        _rule("QPushButton#iconButton, QToolButton#iconButton",
              {"background": "transparent", "border": "1px solid transparent",
               "border-radius": "2px", "padding": f"{hair}px"}),
        _rule("QPushButton#iconButton:hover, QToolButton#iconButton:hover",
              {"background": theme.LIFT, "border-color": rule_strong}),
        _rule("QPushButton#iconButton:focus, QToolButton#iconButton:focus",
              {"border": ring}),
        # The rail: the window's own heading, anchored above every dock.
        _rule("QFrame#rail", {"background-color": theme.SURFACE,
                              "border-bottom": f"1px solid {rule}"}),
        _rule("QFrame#rail QLabel#railTitle", {"font-size": f"{heading_size}pt",
                                               "font-weight": "600"}),
        _rule("QFrame#rail QLabel#railValue",
              {"color": theme.TRACE, "font-size": f"{readout_size}pt",
               "font-weight": "500"}),
        _rule('QFrame#rail QLabel#railValue[quiet="true"], '
              'QFrame#rail QLabel#railValue[lost="true"]',
              {"color": theme.MUTED}),
        _rule("QFrame#rail QLabel#caption, QFrame#rail QLabel#railGroup, "
              "QFrame#rail QLabel#railLost",
              {"font-size": f"{small_size}pt"}),
        _rule("QFrame#rail QLabel#railGroup, QFrame#rail QLabel#railLost",
              {"color": theme.TEXT, "font-weight": "600"}),
        _rule("QFrame#railRule", {"background": rule, "border": "none"}),
        # The alert band: an error that wants acknowledging, under the rail
        # and never over it - the stop stays one press away (F1).
        _rule("QFrame#alertBand", {"background-color": theme.BACKGROUND,
                                   "border-top": f"1px solid {rule}"}),
        _rule("QFrame#alertBand QLabel#alertWord", {"font-weight": "600"}),
        _rule("QFrame#alertBand QLabel#caption", {"color": theme.MUTED}),
        # The tray: one line until it is asked for more.
        _rule("QStatusBar", {"background-color": theme.SURFACE,
                             "border-top": f"1px solid {rule}"}),
        _rule("QStatusBar::item", {"border": "none"}),
        _rule("QFrame#tray", {"background-color": theme.SURFACE}),
        _rule("QFrame#tray QTextEdit", {"background-color": theme.WELL,
                                        "border": "2px solid transparent"}),
        _rule("QFrame#tray QTextEdit:focus", {"border": ring}),
        _rule('QLabel#trayLatest[severity="error"]', {"color": theme.SEVERITY_INK["error"]}),
        _rule('QLabel#trayLatest[severity="warning"]', {"color": theme.SEVERITY_INK["warning"]}),
        _rule('QLabel#trayLatest[severity="info"]', {"color": theme.SEVERITY_INK["info"]}),
        _rule("QLabel#emptyTitle", {"font-size": f"{heading_size}pt",
                                    "font-weight": "600"}),
        # A dock title is a heading, not a caption: the operator finds a panel
        # by reading these. The only navigation there is. A model's dock
        # draws its own bar (`DockTitle`), Setup's is Qt's.
        _rule("QDockWidget", {"color": theme.TEXT, "font-weight": "600"}),
        _rule("QDockWidget::title", {"background": theme.SURFACE,
                                     "color": theme.TEXT,
                                     "border-bottom": f"1px solid {rule}",
                                     "padding": f"{theme.PAD}px {theme.INSET}px"}),
        _rule("QDockWidget::close-button, QDockWidget::float-button",
              {"background": "transparent", "border": "1px solid transparent",
               "border-radius": "2px", "padding": f"{theme.GAP}px"}),
        _rule("QDockWidget::close-button:hover, QDockWidget::float-button:hover",
              {"background": neutral_bg, "border-color": rule_strong}),
        _rule("QWidget#dockTitle", {"background": theme.SURFACE,
                                    "border-bottom": f"1px solid {rule}"}),
        _rule("QWidget#dockTitle QLabel#dockTitleText", {"font-weight": "600"}),
        _rule("QWidget#dockTitle QLabel#railLost", {"color": theme.TEXT,
                                                    "font-size": f"{small_size}pt"}),
        # Tabs appear once a fourth model opens (F22): the selected one on the
        # card colour, the rest quiet; focus is the same ink ring.
        _rule("QTabBar::tab", {"background": theme.BACKGROUND,
                               "color": theme.MUTED,
                               "border": f"1px solid {rule}",
                               "padding": f"{theme.GAP}px {theme.INSET}px"}),
        _rule("QTabBar::tab:selected", {"background": theme.SURFACE,
                                        "color": theme.TEXT}),
        _rule("QTabBar::tab:hover", {"color": theme.TEXT}),
        _rule("QTabBar::tab:focus", {"border": ring}),
        # A panel's scroll area takes Tab (arrow keys scroll it); it shows
        # that it has it, where it used to change nothing at all (AUD-10).
        _rule("QScrollArea#panelScroll", {"border": "2px solid transparent"}),
        _rule("QScrollArea#panelScroll:focus", {"border": ring}),
        _rule("QScrollBar:vertical, QScrollBar:horizontal",
              {"background": theme.BACKGROUND, "border": "none",
               "width": f"{theme.SPACE[4]}px", "height": f"{theme.SPACE[4]}px"}),
        _rule("QScrollBar::handle", {"background": rule_strong,
                                     "border-radius": f"{theme.GAP}px",
                                     "min-height": f"{theme.SPACE[6]}px",
                                     "min-width": f"{theme.SPACE[6]}px"}),
        _rule("QScrollBar::handle:hover", {"background": theme.MUTED}),
        _rule("QScrollBar::add-line, QScrollBar::sub-line",
              {"width": "0px", "height": "0px"}),
        _rule("QToolTip", {"background-color": theme.SURFACE,
                           "color": theme.TEXT,
                           "border": f"1px solid {rule_strong}",
                           "padding": f"{theme.GAP}px"}),
    ]
    return "".join(sheet)


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
# two layouts are two containers with one API rather than an `if` inside each
# of the thirteen builders. Neither is a QWidget - they only place widgets
# someone else made - which is why the module still imports without PySide6.
# ---------------------------------------------------------------------------

def _caption(text):
    """A label beside a control: sentence case, no colon, the muted ink."""
    label = QLabel(sentence_case(text))
    label.setObjectName("caption")
    return label


class ColumnSection:
    """`layout="column"`: a form, one labelled control per line."""

    #: A column section lets a control size itself.
    control_width = 0
    is_row = False

    def __init__(self, form):
        self.form = form

    def add(self, label, widget):
        """One labelled control. An empty label spans the section's width."""
        if label:
            self.form.addRow(_caption(label), widget)
        else:
            self.form.addRow(widget)

    def add_wide(self, label, widget):
        """A control too tall or too wide to sit beside its caption. A button
        keeps its natural width: stretched across the card, a row of them
        read as a stack of bars rather than as commands."""
        if label:
            self.form.addRow(_caption(label))
        if isinstance(widget, QAbstractButton):
            widget = _bare_row(widget, stretch=True)
        self.form.addRow(widget)


def _bare_row(*widgets, stretch=False):
    """Widgets side by side in a holder that paints nothing."""
    holder = QWidget()
    holder.setObjectName("bare")
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(theme.GAP)
    for widget in widgets:
        layout.addWidget(widget)
    if stretch:
        layout.addStretch(1)
    return holder


class PanelTable:
    """The shared grid that every `layout="row"` section adds one line to.

    Columns are allocated **by label**, not by position, so "Port" sits under
    "Port" in every row even in a row that declares no gamepad and no port at
    all. That is what makes the Setup panel read as a table — one line per
    model, aligned — instead of as six ragged lines of different lengths,
    which is what "everything in one vertical tab is poor UI/UX" was about.

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
        """One line. An **untitled** section claims no name column at all.

        A schema that already carries the row's name as an element can title
        the section `""` and get a table with no duplicated name and no dead
        column, without a renderer change.
        """
        if self.header_row is None:
            self.rows += 1
            self.header_row = self.rows
        self.rows += 1
        if title:
            label = QLabel(title)
            label.setObjectName("rowTitle")
            self.grid.addWidget(label, self.rows, 0)
        return TableRow(self, self.rows)

    def add_bar(self, title):
        """An action line across the whole table: its name, its readouts, and
        its commands pushed to the right edge."""
        self.rows += 1
        bar = TableBar(title)
        self.grid.addWidget(bar.widget, self.rows, 0, 1, -1)
        return bar

    def column_for(self, label):
        """This label's column, allocating one the first time it is seen.

        An *unlabelled* widget gets a fresh column of its own: two of them in
        one row are two controls, not one control written twice.
        """
        key = (label or "").strip()
        if not key:
            return self._claim(None)
        if key not in self.columns:
            self.columns[key] = self._claim(key)
        return self.columns[key]

    def _claim(self, header):
        column = self._next_column
        self._next_column += 1
        self.grid.setColumnMinimumWidth(column, TABLE_CONTROL_MIN_PX)
        if header:
            caption = QLabel(sentence_case(header))
            caption.setObjectName("columnHeader")
            self.grid.addWidget(caption, self.header_row or 0, column)
        return column


class TableRow:
    """`layout="row"`: one model's line in the panel's table."""

    control_width = TABLE_CONTROL_MIN_PX
    is_row = True

    def __init__(self, table, row):
        self.table, self.row = table, row

    def add(self, label, widget):
        self.table.grid.addWidget(widget, self.row,
                                  self.table.column_for(label))

    #: A row has one line; "wide" has nothing to mean here.
    add_wide = add


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

    def add(self, label, widget):
        if isinstance(widget, QAbstractButton):
            self._commands.addWidget(widget)
            return
        if label:
            self._values.addWidget(_caption(label))
        if isinstance(widget, QLabel):
            widget.setWordWrap(True)
        self._values.addWidget(widget, 1)

    add_wide = add


# ---------------------------------------------------------------------------
# Pure widgets
# ---------------------------------------------------------------------------

class SeriesPlot(QWidget):
    """The `plot` element's inline drawing surface.

    A `QPainter` polyline, not an embedded matplotlib canvas: this is a live
    readout that ticks at the render rate, and Tk draws the same thing on a
    `tk.Canvas`. Matplotlib's cost bought axes and a toolbar for a *dialog*
    that no longer exists — reviewing a saved run is the model's job now, and
    reaches every view instead of only this one.
    """

    MARGIN_PX = 5

    def __init__(self, role="neutral", parent=None):
        QWidget.__init__(self, parent)
        self.role = role
        self._points = []
        self.setMinimumHeight(140)
        self.setMinimumWidth(240)

    @property
    def points(self):
        return list(self._points)

    def set_series(self, data):
        points = series_points(data)
        if points != self._points:
            self._points = points
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.SURFACE))
        scaled = polyline_points(self._points, self.width(), self.height(),
                                 self.MARGIN_PX)
        if not scaled:
            # An empty plot says so, rather than being an empty box.
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "No samples yet")
            return
        background, _ = theme.colors(self.role)
        line = theme.TEXT if self.role == "neutral" else background
        pen = QPen(QColor(line))
        pen.setWidth(2)
        painter.setPen(pen)
        previous = None
        for point in scaled:
            if previous is not None:
                painter.drawLine(int(previous[0]), int(previous[1]),
                                 int(point[0]), int(point[1]))
            previous = point


class StopButton(QPushButton):
    """The stop object: round, signal red, a darker ring - the one bold thing.

    The same object as the Web view's mushroom, at two sizes: the rail's, and
    one size down in a model's Safety section. It reads `Stop`, and `Clear`
    once the latch is set; the ring lights in the trace colour while latched
    and swells exactly once, on the edge where the latch closes. It is never
    disabled and never dimmed - `setEnabled(False)` is refused here, because
    a gate on the one control that stops things is the defect, not a state.

    Keyboard: Space, Return and Enter all press it (Return and Enter did
    nothing - AUD-4). Focus is an ink ring painted *outside* the disc, in a
    margin kept for it, so it shows in both states and can never be mistaken
    for the trace ring that means latched.
    """

    #: The focus ring's width, and the margin kept around the disc for it.
    FOCUS_PX = 2
    FOCUS_GAP = 3

    def __init__(self, mini=False, parent=None):
        QPushButton.__init__(self, "Stop", parent)
        self.is_mini = bool(mini)
        self.is_latched = False
        self._ring = 0
        self._sheet = None
        self.setObjectName("miniStop" if mini else "stop")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Reached by Tab, not handed focus at boot: a focus ring on it before
        # anything has happened reads as the latch.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._pulse = QVariantAnimation(self)
        self._pulse.setDuration(PULSE_MS)
        self._pulse.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pulse.valueChanged.connect(self._on_pulse)
        self.resize_to_font()
        self._dress()

    @staticmethod
    def line_height():
        """One line of the theme's base font - not this widget's font, which
        the sheet has not reached yet when the disc is sized."""
        family, size, _ = theme.font()
        return QFontMetrics(QFont(family, size)).height()

    def max_px(self):
        """The cap: 96 px for the rail's stop, the same proportion smaller
        for a model's own."""
        if self.is_mini:
            return int(round(STOP_MAX_PX * MINI_STOP_LINES / STOP_DISC_LINES))
        return STOP_MAX_PX

    def resize_to_font(self):
        lines = MINI_STOP_LINES if self.is_mini else STOP_DISC_LINES
        disc = min(int(round(self.line_height() * lines)), self.max_px())
        self._ring = max(3, disc // 16)
        self.setFixedSize(disc, disc)
        self._pulse.setStartValue(float(self._ring))
        self._pulse.setKeyValueAt(0.45, float(self._ring) * 2.0)
        self._pulse.setEndValue(float(self._ring))

    def face_size(self):
        """The face's point size: its type step, or smaller if "Clear" would
        not sit inside the ring - which is what a capped disc needs at 28 pt."""
        family = theme.FONT_FAMILY
        size = theme.size(STEP_CAPTION if self.is_mini else STOP_FACE_STEP)
        room = self.width() - 2 * (self.FOCUS_GAP + self._ring + theme.GAP)
        while size > 8:
            font = QFont(family, size)
            font.setBold(True)
            if QFontMetrics(font).horizontalAdvance("Clear") <= room:
                break
            size -= 1
        return size

    def setEnabled(self, enabled):         # noqa: N802 - Qt's name
        """Never dimmed: the stop answers in every mode."""
        QPushButton.setEnabled(self, True)

    def set_latched(self, is_latched):
        """Face and ring from the state, never from the last click. Called on
        every tick; it restyles only when something it draws has changed."""
        is_latched = bool(is_latched)
        was = self.is_latched
        self.is_latched = is_latched
        wanted = "Clear" if is_latched else "Stop"
        if self.text() != wanted:
            self.setText(wanted)
        if is_latched and not was:
            self._pulse.stop()
            if not motion_reduced():
                self._pulse.start()       # once, on the edge
        elif not is_latched:
            self._pulse.stop()
        self._dress()

    def _on_pulse(self, value):
        self._dress(ring=int(round(value)))

    def _dress(self, ring=None):
        ring = self._ring if ring is None else ring
        background, foreground = theme.colors(DANGER_ROLE)
        edge = (theme.TRACE if self.is_latched
                else theme.mix(background, theme.BACKGROUND, 0.52))
        pressed = theme.mix(background, theme.BACKGROUND, 0.68)
        gap = self.FOCUS_GAP
        radius = self.width() // 2 - gap
        name = self.objectName()
        sheet = (f"QPushButton#{name} {{ background-color: {background}; "
                 f"color: {foreground}; border: {ring}px solid {edge}; "
                 f"border-radius: {radius}px; margin: {gap}px; padding: 0px; "
                 f"font-size: {self.face_size()}pt; font-weight: bold; }}"
                 f"QPushButton#{name}:pressed {{ border-color: {pressed}; }}")
        if sheet != self._sheet:          # F21: no re-polish when unchanged
            self._sheet = sheet
            self.setStyleSheet(sheet)

    # -- Qt event handlers ------------------------------------------------
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
        super().paintEvent(event)
        if not self.hasFocus():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(theme.STOP_FOCUS))
        pen.setWidthF(self.FOCUS_PX)
        painter.setPen(pen)
        inset = self.FOCUS_PX / 2.0
        painter.drawEllipse(QRectF(inset, inset, self.width() - 2 * inset,
                                   self.height() - 2 * inset))
        painter.end()


class ElidedLabel(QLabel):
    """One line that never clips a word: too long, it ends in an ellipsis and
    the whole text is in the tooltip.

    `min_chars` makes it a layout citizen: it asks for its whole text, and
    gives way down to that many characters and an ellipsis - the rail's
    captions elide before their group wraps (F5). Without it, it takes what
    it is given (the tray's line).
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
            head += "\u2026"
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


def reload_icon(size=14):
    """A rescan glyph drawn from geometry in the theme's inks: an open ring
    and its arrowhead. Muted at rest, ink when the pointer is on it."""
    icon = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, theme.MUTED),
                      (QIcon.Mode.Active, theme.TEXT),
                      (QIcon.Mode.Disabled, theme.DISABLED[1])):
        pixmap = QPixmap(size * 2, size * 2)
        pixmap.setDevicePixelRatio(2.0)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(ink))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = 2.5
        box = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
        painter.drawArc(box, 90 * 16, 280 * 16)
        tip_x, tip_y = size / 2.0, inset
        painter.drawLine(int(tip_x), int(tip_y), int(tip_x - 3), int(tip_y - 2.5))
        painter.drawLine(int(tip_x), int(tip_y), int(tip_x - 3), int(tip_y + 2.5))
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def target_px():
    """A small control's side: at least 24 px (WCAG 2.5.8), growing with the
    font - the rescan button was 24x20 at every size, the dock's close a
    10 px glyph (AUD-12)."""
    return max(24, int(round(StopButton.line_height() * 1.5)))


def lamp_px():
    """A lamp's diameter: 16 px, or four fifths of a line above that."""
    return max(LAMP_PX, int(round(StopButton.line_height() * 0.8)))


def close_icon(size=14):
    """A close glyph drawn from geometry in the theme's inks - two strokes,
    the rescan glyph's weight. Muted at rest, ink under the pointer."""
    icon = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, theme.MUTED),
                      (QIcon.Mode.Active, theme.TEXT),
                      (QIcon.Mode.Disabled, theme.DISABLED[1])):
        pixmap = QPixmap(size * 2, size * 2)
        pixmap.setDevicePixelRatio(2.0)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(ink))
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = size * 0.25
        painter.drawLine(QPoint(int(inset), int(inset)),
                         QPoint(int(size - inset), int(size - inset)))
        painter.drawLine(QPoint(int(size - inset), int(inset)),
                         QPoint(int(inset), int(size - inset)))
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


def mark(colour, width=None):
    """A severity or fault mark: a small solid bar in `colour`, beside the
    word that says the same thing (F14 - never colour alone). Its colour is
    set once here, never on a tick."""
    bar = QFrame()
    bar.setObjectName("mark")
    bar.setFixedWidth(width or theme.GAP)
    bar.setStyleSheet(f"QFrame#mark {{ background-color: {colour}; "
                      f"border: none; border-radius: 1px; }}")
    return bar


#: Confirmations waiting for an answer. The stop closes them, answering No,
#: before it acts: an open question never stands between the operator and
#: the stop, and never answers Yes for them afterwards.
_PENDING_CONFIRMS = []


def ask(parent, prompt, title="Confirm"):
    """A yes/no question that does not block the stop (F1, F17).

    `QMessageBox.question` is application-modal: while one was up, the rail's
    stop could not be pressed. This one is modeless - the rest of the window
    keeps working - and waits in a local event loop so the caller still gets
    a bool. No is the default and Escape is No: Return never clears a latch
    by accident.
    """
    box = QMessageBox(QMessageBox.Icon.Question, title, prompt,
                      QMessageBox.StandardButton.Yes
                      | QMessageBox.StandardButton.No, parent)
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


class DockTitle(QWidget):
    """A model dock's own title bar: the name, a fault mark when a device is
    lost, and a close button big enough to hit (AUD-12) that says what it
    does. Closing a model stops and disconnects it, so the button is named
    for that, not a bare 10 px glyph."""

    def __init__(self, dock, title):
        QWidget.__init__(self, dock)
        self.setObjectName("dockTitle")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.INSET, theme.GAP, theme.GAP, theme.GAP)
        layout.setSpacing(theme.PAD)
        self.lost_mark = mark(theme.SIGNAL)
        self.lost_mark.setVisible(False)
        layout.addWidget(self.lost_mark)
        self.label = ElidedLabel(min_chars=6)
        self.label.setObjectName("dockTitleText")
        self.label.set_full_text(title)
        layout.addWidget(self.label, 1)
        self.lost_label = QLabel("")
        self.lost_label.setObjectName("railLost")
        self.lost_label.setVisible(False)
        layout.addWidget(self.lost_label)
        side = target_px()
        self.close_button = QToolButton()
        self.close_button.setObjectName("iconButton")
        self.close_button.setIcon(close_icon())
        self.close_button.setIconSize(QSize(side // 2, side // 2))
        self.close_button.setFixedSize(side, side)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.close_button.setToolTip(f"Close {title} (stops and disconnects it)")
        self.close_button.setAccessibleName(f"Close {title}")
        self.close_button.clicked.connect(dock.close)
        layout.addWidget(self.close_button)

    def set_lost(self, text):
        """A lost device, said in the dock's own bar: a signal mark and the
        words, or nothing."""
        shown = bool(text)
        if self.lost_label.text() != text:
            self.lost_label.setText(text)
        if self.lost_mark.isVisible() != shown:
            self.lost_mark.setVisible(shown)
            self.lost_label.setVisible(shown)


class DeviceDock(QDockWidget):
    """One model's panel, in a closable dock. Closing it closes the model."""

    closed = Signal()

    def __init__(self, title, parent=None):
        QDockWidget.__init__(self, title, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        # Movable and closable; not floatable. A floating panel is a second
        # window the rail's stop does not sit on, and its glyph was clutter.
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.title_bar = DockTitle(self, title)
        self.setTitleBarWidget(self.title_bar)

    def set_lost(self, text):
        self.title_bar.set_lost(text)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# The panel renderer
# ---------------------------------------------------------------------------

class QtPanelView(PanelView, QWidget):
    """One panel, rendered from its schema. Was `QtDynamicView`.

    Every element type in `schema.ELEMENT_TYPES` has a `_make_` here, which is
    what `PanelView.__init__` checks before it will construct: a renderer that
    cannot draw an element type fails loudly at build time instead of quietly
    skipping the control.
    """

    def __init__(self, controller, name, panel=None, parent=None):
        QWidget.__init__(self, parent)
        PanelView.__init__(self, controller, name, panel)
        self._widgets = {}          # id(element) -> widget
        self._clean_text = {}       # id(element) -> last text we wrote
        self._overlay = None
        self._table = None          # built on the first layout="row" section

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(theme.PAD, theme.PAD, theme.PAD,
                                        theme.PAD)
        self._layout.setSpacing(theme.PAD)
        self._columns, self._split = self._plan_columns()
        self._cards_placed = 0
        # One flag per section, in schema order: `_make_section` is handed a
        # title and a layout, and an action line is decided by what the
        # section holds.
        self._action_rows = [is_action_row(s)
                             for s in self._schema()["sections"]]
        self._sections_made = 0
        self._refusals = {}         # id(card) -> that card's refusal line
        self._refusal_shown = None  # the refusal line on screen, if any
        self._acting = None         # the element whose command is running
        self._last_state = None
        self._styled = {}           # id(element) -> the sheet it was given
        self._lost = []             # devices the model reports lost
        self._is_stale = False
        self._closed = False
        # What the operator must know before reading anything else sits at
        # the top of the panel, not under the last card: a lost device, stale
        # readings, and a refusal from a command that has no card.
        self.notice = QLabel("")
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        self.stale_label = QLabel("Readings are stale")
        self.stale_label.setObjectName("staleLabel")
        self.stale_label.setVisible(False)
        self._panel_refusal = self._refusal_line()
        # Inserted, not added: a two-column panel has already put its columns
        # into this layout.
        self._layout.insertWidget(0, self._line_with_mark(self.notice,
                                                          theme.SIGNAL))
        self._layout.insertWidget(1, self.stale_label)
        self._layout.insertWidget(2, self._panel_refusal)

        self._build()

        for column in self._columns:
            column.addStretch()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start(self.REFRESH_MS)
        events.debug("Panel Opened", f"{self.name} rendered "
                     f"{len(self._elements)} elements", source="QtView")

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
        row.addWidget(mark(colour))
        row.addWidget(label, 1)
        holder.setVisible(False)
        label.setVisible(True)
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
        if self._timer is not None:
            self._timer.stop()
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None
        PanelView.close(self)
        events.debug("Panel Closed", self.name, source="QtView")
        return QWidget.close(self)

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

    # -- how many columns the panel is laid out in --------------------------
    def _plan_columns(self):
        """(column layouts, index of the first card in the second column).

        A panel is one column until it has more than `COLUMN_SPLIT_CARDS`
        cards, and then two. Red Percent declares ten sections; stacked, its
        dock was twice the height of the screen and Safety - the section that
        must stay reachable - sat below the fold. Two columns keep it on
        screen without a scroll area, which would hide it just as well.

        The count is of **cards**, not sections: every `layout="row"` section
        shares one table card, so Setup's eight rows are one card and stay in
        one column.
        """
        sections = self._schema()["sections"]
        cards = sum(1 for s in sections
                    if s.get("layout", "column") != "row")
        if any(s.get("layout") == "row" for s in sections):
            cards += 1
        if cards <= COLUMN_SPLIT_CARDS:
            return [self._layout], 0
        holder = QHBoxLayout()
        holder.setContentsMargins(0, 0, 0, 0)
        holder.setSpacing(theme.PAD)
        columns = []
        for _ in range(2):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(theme.PAD)
            holder.addLayout(column, 1)
            columns.append(column)
        self._layout.addLayout(holder)
        return columns, (cards + 1) // 2

    def _place_card(self, card, alignment=None):
        """Put one section card in the column it belongs to."""
        second = bool(self._split) and self._cards_placed >= self._split
        column = self._columns[-1 if second else 0]
        if alignment is None:
            column.addWidget(card)
        else:
            column.addWidget(card, 0, alignment)
        self._cards_placed += 1

    # -- sections ----------------------------------------------------------
    def _make_section(self, title, layout="column"):
        """The container the section's elements are built into.

        `layout` comes straight from `schema.section(..., layout=)` and is a
        hint every renderer has to honour (Addendum 2): `"row"` lays the
        elements out horizontally, one line per section, in the panel's shared
        table. Setup declares one row section per model type, so what the
        operator sees is a table with one line per model rather than six
        stacked forms.
        """
        index = self._sections_made
        self._sections_made += 1
        if layout == "row":
            table = self._panel_table()
            if index < len(self._action_rows) and self._action_rows[index]:
                return table.add_bar(title)
            return table.add_row(title)
        return self._column_section(title)

    def _column_section(self, title):
        card = QFrame()
        card.setObjectName("card")
        # Maximum, not the default Preferred: a card keeps its natural height
        # and the panel's trailing stretch takes the slack. Left to expand,
        # the stepper's System Control card grew to a third of the dock with
        # its title floating in the middle of the empty space.
        card.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Maximum)
        # Title, then the body indented under it by `theme.INSET` - which is
        # what that theme value is for. A QFormLayout cannot indent one of its
        # own rows, so the title is a sibling of the form rather than a row
        # in it.
        outer = QVBoxLayout(card)
        outer.setContentsMargins(theme.PAD, theme.PAD, theme.PAD, theme.PAD)
        outer.setSpacing(theme.GAP)
        label = QLabel(sentence_case(title))
        label.setObjectName("sectionTitle")
        outer.addWidget(label)
        form = QFormLayout()
        form.setContentsMargins(theme.INSET, 0, 0, 0)
        form.setHorizontalSpacing(theme.INSET)
        form.setVerticalSpacing(theme.GAP)
        # Fields at their natural size: grown to the card's width, an entry
        # for a three-digit speed was 800 px of empty well.
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        form.setFormAlignment(Qt.AlignmentFlag.AlignLeft
                              | Qt.AlignmentFlag.AlignTop)
        outer.addLayout(form)
        outer.addWidget(self._card_refusal(card))
        self._place_card(card)
        return ColumnSection(form)

    def _card_refusal(self, card):
        """Every card keeps a refusal line at its foot, hidden until a command
        in that card is refused (F10)."""
        line = self._refusal_line()
        self._refusals[id(card)] = line
        return line

    def _panel_table(self):
        """One table per panel, built where its first row section appears.

        Every later row section joins it rather than starting a card of its
        own, which is the only way their columns can line up.
        """
        if self._table is None:
            card = QFrame()
            card.setObjectName("card")
            # As wide as its columns and no wider: stretched across a 1400 px
            # window, the Status column ended half a screen from its port.
            card.setSizePolicy(QSizePolicy.Policy.Maximum,
                               QSizePolicy.Policy.Maximum)
            outer = QVBoxLayout(card)
            outer.setContentsMargins(theme.PAD, theme.PAD, theme.PAD, theme.PAD)
            outer.setSpacing(theme.GAP)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            outer.addLayout(grid)
            outer.addWidget(self._card_refusal(card))
            grid.setHorizontalSpacing(theme.INSET)
            grid.setVerticalSpacing(theme.GAP)
            # Column 0's width is claimed by the first *titled* row, so a
            # table of untitled rows costs nothing.
            grid.setColumnStretch(0, 0)
            self._place_card(card, Qt.AlignmentFlag.AlignLeft)
            self._table = PanelTable(grid)
        return self._table

    # -- element builders --------------------------------------------------
    def _make_readonly(self, container, element):
        value = ReadoutLabel(EMPTY_READOUT)
        value.setObjectName("valueLabel")
        value.setProperty("quiet", "true")
        value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._remember(element, value)
        container.add(element.get("text", ""), value)

    def _make_entry(self, container, element):
        entry = QLineEdit()
        self._install_validator(entry, element)
        if container.control_width:
            entry.setMinimumWidth(container.control_width)
        self._remember(element, entry)
        container.add(element.get("text", ""), entry)

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
        container.add_wide("", button)

    def _make_toggle(self, container, element):
        if DANGER_ROLE in (element.get("on_role"), element.get("off_role")):
            # A model's own stop is the stop object one size down - the same
            # round red thing as the rail's, reading Stop / Clear - so the
            # operator never has to work out which control stops this model.
            # It rendered as a full-width "FULL STOP" slab, then as a red slab
            # reading "LATCHED - click to clear": two reds that were not the
            # stop object. The schema's own words are its tooltip.
            button = StopButton(mini=True)
            button.setToolTip(sentence(element.get("false_text", "")))
        else:
            button = QPushButton(sentence_case(element.get("false_text", "Off")))
        button.clicked.connect(lambda: self._run_toggle(element))
        self._remember(element, button)
        container.add(element.get("text", ""), button)

    def _make_dropdown(self, container, element):
        combo = MiddleCombo()
        # Sized to a fixed number of characters rather than to its longest
        # option: a column of dropdowns is one width, not four.
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(DROPDOWN_CHARS)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                            QSizePolicy.Policy.Fixed)
        self._remember(element, combo)
        self._reload_options(element, combo)
        combo.currentTextChanged.connect(
            lambda text: self._on_dropdown_changed(element, text))
        # A quiet icon, not a dark block beside every dropdown: it is the
        # rarely-needed way to pick up a port plugged in after the panel was
        # built, and it says so on hover.
        refresh = QPushButton()
        refresh.setObjectName("iconButton")
        side = target_px()
        refresh.setIcon(reload_icon(side * 7 // 12))
        refresh.setIconSize(QSize(side * 7 // 12, side * 7 // 12))
        refresh.setFixedSize(side, side)
        refresh.setToolTip("Rescan the choices")
        refresh.setAccessibleName("Rescan the choices")
        refresh.clicked.connect(lambda: self._reload_options(element, combo))
        cell = self._row(combo, refresh)
        if container.control_width:
            cell.setMinimumWidth(container.control_width)
        container.add(element.get("text", ""), cell)

    def _make_region_select(self, container, element):
        button = QPushButton(sentence_case(element.get("text", "Select region")))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._pick_region(element))
        value = QLabel(sch.format_region(None))
        value.setObjectName("valueLabel")
        self._remember(element, value)
        container.add("", _bare_row(button, value, stretch=True))

    def _make_file_save(self, container, element):
        button = QPushButton(sentence_case(element.get("text", "Save")))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._save_to_file(element))
        self._remember(element, button)
        container.add_wide("", button)

    def _make_file_open(self, container, element):
        button = QPushButton(sentence_case(element.get("text", "Open")))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._open_from_file(element))
        self._remember(element, button)
        container.add_wide("", button)

    def _make_plot(self, container, element):
        plot = SeriesPlot(role=element.get("role", "neutral"))
        plot.setToolTip(f"{element.get('x_label', '')} / "
                        f"{element.get('y_label', '')}".strip(" /"))
        self._remember(element, plot)
        container.add_wide(element.get("text", ""), plot)

    def _make_image(self, container, element):
        label = QLabel("")
        label.setObjectName("valueLabel")
        label.setMinimumHeight(140)
        self._remember(element, label)
        container.add_wide(element.get("text", ""), label)

    def _make_indicator(self, container, element):
        """A lamp, not a second copy of the caption.

        This was a full-width `valueLabel` whose text `_set_on` filled in from
        the element - and the element's text is what the row's own caption
        already says, so every indicator rendered as "Fault    Fault". A
        boolean readout is a filled dot or an outlined one; the words beside
        it are the label's job.
        """
        lamp = QLabel("")
        lamp.setObjectName("lamp")
        side = lamp_px()
        lamp.setFixedSize(side, side)
        caption = sentence_case(element.get("text", ""))
        lamp.setAccessibleName(f"{caption}: off")
        lamp.setToolTip(f"{caption}: off")
        self._remember(element, lamp)
        container.add(element.get("text", ""), lamp)

    def _make_log_stream(self, container, element):
        view = QTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        # Fixed, not merely minimum: a QTextEdit takes every spare pixel a
        # form layout will give it.
        view.setFixedHeight(LOG_STREAM_PX)
        self._remember(element, view)
        container.add_wide(element.get("text", ""), view)

    def _make_internal(self, container, element):
        """Registers a command in the schema's allow-list and draws nothing.

        PYSIDE-17: this used to fall through to `addRow(row_layout)` with an
        empty layout, adding a blank gap for a control that does not exist.
        """
        return None

    # -- what base.PanelView calls ----------------------------------------
    def _read_entry(self, element):
        widget = self._widget_for(element)
        return widget.text() if widget is not None else ""

    def _entry_is_dirty(self, element):
        """The operator is mid-edit: refresh must not overwrite the box.

        Dirty means *focused and changed from what we last wrote*, so a
        focused-but-untouched field still follows the model, and an
        unfocused one always does.
        """
        widget = self._widget_for(element)
        if widget is None or not self._is_focused(widget):
            return False
        return widget.text() != self._clean_text.get(id(element), "")

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
            self._set_readout(widget, text)
            self._clean_text[id(element)] = text
            return
        if widget.text() != text:
            widget.setText(text)
        self._clean_text[id(element)] = text

    @staticmethod
    def _set_readout(widget, text):
        """A readout: its value in the trace ink, or muted when it is at rest
        ("off", "False", nothing yet). Empty is a dash, never a blank."""
        text = as_operator_word(text)
        shown = text if str(text).strip() else EMPTY_READOUT
        if widget.text() != shown:
            widget.setText(shown)
        quiet = "true" if is_quiet_value(text) else "false"
        if widget.property("quiet") != quiet:
            widget.setProperty("quiet", quiet)
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _set_on(self, element, is_on):
        widget = self._widget_for(element)
        if widget is None:
            return
        if isinstance(widget, StopButton):
            widget.set_latched(is_on)
            wanted = sentence(element.get("true_text" if is_on else "false_text", ""))
            if widget.toolTip() != wanted:
                widget.setToolTip(wanted)
            return
        if element["type"] == "toggle":
            colours = theme.toggle_colors(element, is_on)
            # A selector, not bare declarations, so the sheet's hover and
            # focus states still reach a toggle.
            self._restyle(element, widget,
                f"QPushButton {{ background-color: {colours['background']}; "
                f"color: {colours['foreground']}; "
                f"border: 1px solid {colours['border']}; border-radius: 2px; }}"
                f"QPushButton:hover {{ border-color: {theme.MUTED}; }}"
                f"QPushButton:focus {{ border: {FOCUS_RING}; }}")
            wanted = sentence_case(element.get("true_text" if is_on
                                               else "false_text", ""))
            if widget.text() != wanted:
                widget.setText(wanted)
            return
        # An indicator: a lamp, and no text at all. Writing `element["text"]`
        # here is what made every indicator read its own caption twice.
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
        widget = self._widget_for(element)
        if widget is not None and widget.isEnabled() != is_enabled:
            widget.setEnabled(is_enabled)

    def _set_stale(self, is_stale):
        self._is_stale = bool(is_stale)
        self.stale_label.setVisible(self._is_stale and not self._lost)
        self._sync_dim()

    def _sync_dim(self):
        """Readouts dim when the readings are stale or a device is lost: in
        both cases a number on screen is no longer what the hardware says."""
        dim = "true" if (self._is_stale or self._lost) else "false"
        if self.property("stale") == dim:
            return
        self.setProperty("stale", dim)
        # A descendant selector is only re-read when the *descendant* is
        # polished: polishing the panel alone left every readout in trace,
        # so a stale panel never actually dimmed.
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
        self._set_lost(lost_devices(self._last_state))

    _sync_gates = _refresh

    @property
    def lost_devices(self):
        return list(self._lost)

    def _set_lost(self, lost):
        """The model says a device is lost: say so at the top of the panel,
        in ink beside a fault mark, and dim every readout. The dock's bar and
        the rail carry the same news (`QtDashboard._sync_readouts`)."""
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
        """The refusal line of the card that holds this element's control, or
        the panel's own line when it has none."""
        widget = self._widget_for(element) if element is not None else None
        while widget is not None:
            line = self._refusals.get(id(widget))
            if line is not None:
                return line
            widget = widget.parentWidget()
        return self._panel_refusal

    def _show_refused(self, reason):
        """A refusal is a line in the card of the control that caused it, not
        a dialog and not a line at the foot of the panel that may be 900 px
        below the fold (F10, HC-5). It is scrolled into view, and the next
        command that succeeds clears it.

        Both desktop views used to drop `Refused` on the floor entirely; the
        answer is not a modal per refusal but one non-modal line the operator
        can read and act on.
        """
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
        """Bring `widget` into the panel's scroll area's view, if it has one."""
        holder = self.parentWidget()
        while holder is not None and not isinstance(holder, QScrollArea):
            holder = holder.parentWidget()
        if holder is not None and widget.isVisible():
            holder.ensureWidgetVisible(widget, 0, theme.PAD)

    def _apply_theme(self):
        """One sheet, on the QApplication.

        PYSIDE-15: applying it to the window left every `QMessageBox` and file
        dialog in the native light palette, floating unparented over a dark
        dashboard. The application owns the look.
        """
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
        widget = self._widget_for(element)
        if widget is None:
            return
        lines = data if isinstance(data, (list, tuple)) else str(data or "").splitlines()
        text = "\n".join(str(line) for line in list(lines)[-200:])
        if widget.toPlainText() != text:
            widget.setPlainText(text)
            # `QTextCursor.MoveOperation.End`, not `QTextCursor.End`: the
            # latter is not an instance attribute in PySide6 and raised
            # AttributeError out of the render tick, skipping every widget
            # after it in that tick.
            widget.moveCursor(QTextCursor.MoveOperation.End)

    def _redraw_image(self, element, data):
        widget = self._widget_for(element)
        if widget is None or not data:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(bytes(data)):
            widget.setPixmap(pixmap)
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

    def _widget_for(self, element):
        return self._widgets.get(id(element))

    @staticmethod
    def _row(*widgets):
        return _bare_row(*widgets)


# ---------------------------------------------------------------------------
# The rail's readouts
# ---------------------------------------------------------------------------

class RailGroup(QWidget):
    """One open model on the rail: its name over its key numbers.

    A number is a plain label that is never squeezed: it asks for its whole
    width and gets it. The captions and the model's name are the give - they
    elide, to a few characters and an ellipsis, with the whole text in the
    tooltip. A clipped "0." that was really 0.00 read as another value (F5,
    AUD-1).
    """

    def __init__(self, name, elements, parent=None):
        QWidget.__init__(self, parent)
        self.setObjectName("bare")
        self.name = name
        self._shown = len(elements)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.INSET)
        rule = QFrame()
        rule.setObjectName("railRule")
        rule.setFixedWidth(1)
        outer.addWidget(rule)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(theme.GAP)
        self.heading = ElidedLabel(min_chars=8)
        self.heading.setObjectName("railGroup")
        self.heading.set_full_text(name)
        head.addWidget(self.heading)
        # A lost device: a fault mark and the words, beside the model's name.
        self.lost_mark = mark(theme.SIGNAL)
        self.lost_mark.setVisible(False)
        head.addWidget(self.lost_mark)
        self.lost_label = QLabel("")
        self.lost_label.setObjectName("railLost")
        self.lost_label.setVisible(False)
        head.addWidget(self.lost_label)
        head.addStretch(1)
        column.addLayout(head)
        self._numbers = QHBoxLayout()
        self._numbers.setContentsMargins(0, 0, 0, 0)
        self._numbers.setSpacing(theme.GAP)
        self.readouts = []
        self._pairs = []
        for index, element in enumerate(elements):
            if index:
                self._numbers.addSpacing(theme.PAD)
            caption = ElidedLabel(min_chars=RAIL_CAPTION_CHARS)
            caption.setObjectName("caption")
            caption.set_full_text(sentence_case(element.get("text") or
                                                element.get("model_attr", "")))
            value = QLabel(EMPTY_READOUT)
            value.setObjectName("railValue")
            value.setProperty("quiet", "true")
            value.setProperty("lost", "false")
            value.setSizePolicy(QSizePolicy.Policy.Fixed,
                                QSizePolicy.Policy.Preferred)
            font = value.font()
            try:                          # tabular figures, where Qt has them
                font.setFeature(QFont.Tag("tnum"), 1)
                value.setFont(font)
            except (AttributeError, TypeError):
                pass
            self._numbers.addWidget(caption, 0, Qt.AlignmentFlag.AlignBaseline)
            self._numbers.addWidget(value, 0, Qt.AlignmentFlag.AlignBaseline)
            self.readouts.append((element, value))
            self._pairs.append((caption, value))
        self._numbers.addStretch(1)
        column.addLayout(self._numbers)
        outer.addLayout(column, 1)
        self._layouts = (self._numbers, head, column, outer)

    def show_readouts(self, count):
        """Show the first `count` readouts; the rest are in the tooltip."""
        count = max(1, min(count, len(self._pairs))) if self._pairs else 0
        if count == self._shown:
            return False
        self._shown = count
        for index, (caption, value) in enumerate(self._pairs):
            caption.setVisible(index < count)
            value.setVisible(index < count)
        for layout in self._layouts:
            layout.invalidate()
        self._sync_tip()
        return True

    @property
    def shown(self):
        return self._shown

    def _sync_tip(self):
        tip = ""
        if self._shown < len(self._pairs):
            tip = self.name + ": " + ", ".join(
                f"{caption.full_text()} {value.text()}"
                for caption, value in self._pairs)
        if self.toolTip() != tip:
            self.setToolTip(tip)

    def set_value(self, value_label, text):
        """Returns True when the number's width changed, so the rail can
        re-pack: a value that grows from a dash to 12345.678 must not be
        drawn in the dash's room."""
        QtPanelView._set_readout(value_label, text)
        grew = False
        if value_label.minimumWidth() != value_label.sizeHint().width():
            # Whole, always: the number asks for exactly its own width.
            value_label.setMinimumWidth(value_label.sizeHint().width())
            for layout in self._layouts:
                layout.invalidate()
            grew = True
        if self._shown < len(self._pairs):
            self._sync_tip()
        return grew

    def set_lost(self, text):
        """A lost device: the words beside the name, the numbers muted."""
        shown = bool(text)
        if self.lost_label.text() != text:
            self.lost_label.setText(text)
        if self.lost_mark.isVisible() != shown:
            self.lost_mark.setVisible(shown)
            self.lost_label.setVisible(shown)
        flag = "true" if shown else "false"
        for _, value in self._pairs:
            if value.property("lost") != flag:
                value.setProperty("lost", flag)
                value.style().unpolish(value)
                value.style().polish(value)


class RailReadouts(QWidget):
    """The rail's readout groups, laid out so a number is never clipped.

    In order, as the plan asks (F5): a group gives way first by eliding its
    captions; groups that still do not fit wrap onto a second line; past
    `RAIL_LINES` lines, every model shows one readout fewer until they fit.
    The stop is not in here - it sits beside this widget on the rail and is
    never part of the negotiation.
    """

    SPACING = theme.SPACE[6]

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self.setObjectName("railReadouts")
        self.groups = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

    def add_group(self, group):
        group.setParent(self)
        group.show()
        self.groups.append(group)
        self.relayout()

    def remove_group(self, group):
        if group in self.groups:
            self.groups.remove(group)
        group.hide()
        group.deleteLater()
        self.relayout()

    def relayout(self):
        self.updateGeometry()
        self._arrange(self.width())

    @property
    def lines(self):
        return getattr(self, "_lines", 0)

    def sizeHint(self):
        if not self.groups:
            return QSize(0, 0)
        width = (sum(g.sizeHint().width() for g in self.groups)
                 + self.SPACING * (len(self.groups) - 1))
        height = max(g.sizeHint().height() for g in self.groups)
        return QSize(width, max(height, self.minimumHeight()))

    def minimumSizeHint(self):
        if not self.groups:
            return QSize(0, 0)
        return QSize(max(g.minimumSizeHint().width() for g in self.groups),
                     self.minimumHeight())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._arrange(event.size().width())

    def _pack(self, width):
        """Greedy lines by each group's minimum width."""
        rows, row, used = [], [], 0
        for group in self.groups:
            need = group.minimumSizeHint().width()
            extra = need + (self.SPACING if row else 0)
            if row and used + extra > width:
                rows.append(row)
                row, used = [], 0
                extra = need
            row.append(group)
            used += extra
        if row:
            rows.append(row)
        return rows

    def _arrange(self, width):
        if not self.groups or width <= 0:
            self._lines = 0
            if self.minimumHeight():
                self.setMinimumHeight(0)
            return
        most = max(len(g.readouts) for g in self.groups) or 1
        for count in range(most, 0, -1):
            for group in self.groups:
                group.show_readouts(count)
            rows = self._pack(width)
            widest = max(g.minimumSizeHint().width() for g in self.groups)
            if (len(rows) <= RAIL_LINES and widest <= width) or count == 1:
                break
        self._lines = len(rows)
        heights = [max(g.sizeHint().height() for g in row) for row in rows]
        total = sum(heights) + theme.PAD * (len(rows) - 1)
        # Centred on the rail's line, as the stop and the title are.
        top = max(0, (self.height() - total) // 2)
        for row in rows:
            height = max(g.sizeHint().height() for g in row)
            mins = [g.minimumSizeHint().width() for g in row]
            hints = [max(g.sizeHint().width(), m) for g, m in zip(row, mins)]
            spare = max(0, width - sum(mins) - self.SPACING * (len(row) - 1))
            give = sum(h - m for h, m in zip(hints, mins))
            left = 0
            for group, low, high in zip(row, mins, hints):
                share = (high - low) if give <= spare else (
                    (high - low) * spare // give if give else 0)
                group.setGeometry(left, top, low + share, height)
                left += low + share + self.SPACING
            top += height + theme.PAD
        if self.minimumHeight() != total:
            self.setMinimumHeight(total)
            self.updateGeometry()


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

class QtDashboard(Dashboard, QMainWindow):
    """The dashboard window: a rail, Setup, one dock per open model, a tray.

    Absorbs `DashboardWindow`, `QtEventLogPanel` and `QtErrorPopupManager`.

    Layout, as the Web view's sibling (docks where it has a drawer):

        +- rail (the menu widget: above every dock, never covered) -----------+
        | Transfer stage   Stepper Probe  x 0  y 0  z 0   ...  [Setup] (Stop) |
        +---------------------------------------------------------------------+
        | Setup (top dock; put away on launch, back from the rail's Setup)     |
        | model docks, side by side - or, with none open, what to do next     |
        +- tray: the latest event on one line ---------------- [Show events] -+

    One navigation. The model docks' own titles name the panels; there is no
    second row of tabs repeating them, and no sidebar repeating them again.
    """

    #: Every cross-thread hop goes through this, queued. See the module
    #: docstring: AutoConnection is what PYSIDE-21 was.
    _marshalled = Signal(object)

    STOP_REFRESH_MS = 250

    def __init__(self, controller, setup):
        QMainWindow.__init__(self)
        Dashboard.__init__(self, controller, setup)
        self._docks = {}
        self._panels = {}           # name -> QtPanelView inside its dock
        self._last_dock = None
        self._is_focused = True
        self._setup_dock = None
        self.setup_action = None
        self._rail_groups = {}      # name -> (RailGroup, [(element, value label)])
        self._closed_shown = None
        self._lost = {}             # name -> the devices it reports lost
        self._alerts = []           # errors waiting to be acknowledged
        self._raise_on_add = None   # a model being reopened

        self.setWindowTitle("Transfer Stage")
        self.resize(1400, 900)
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks
                            | QMainWindow.DockOption.AllowTabbedDocks)
        # Setup spans the window's width above the models.
        self.setCorner(Qt.Corner.TopLeftCorner, Qt.DockWidgetArea.TopDockWidgetArea)
        self.setCorner(Qt.Corner.TopRightCorner, Qt.DockWidgetArea.TopDockWidgetArea)
        self._marshalled.connect(self._on_marshalled, Qt.QueuedConnection)

        self._build_rail()
        self._build_empty_state()
        self._build_event_tray()

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
        return application

    def wait(self):
        """Run the Qt event loop until the window closes. Blocks the launching
        thread like Tk's mainloop; `close()` runs on aboutToQuit."""
        application = QApplication.instance()
        if application is None:
            return 0
        return application.exec()

    def open(self):
        """Subscribe, show the Setup panel, show the window.

        Setup is a `Panel` the Controller does not own, so it is passed to the
        panel view directly. There is no separate wizard window and no second
        copy of the device list: one setup, rendered like everything else.
        """
        Dashboard.open(self)
        # A plain QDockWidget, not a `DeviceDock`: closing Setup puts it away,
        # it does not close a model, and it must NOT be deleted on close the
        # way a model's dock is - the panel behind it keeps the scan results.
        # Closable is also load-bearing: Qt *disables* `toggleViewAction` on a
        # dock that cannot be closed, so without it the rail's Setup is dead.
        self._setup_dock = QDockWidget("Setup", self)
        self._setup_dock.setObjectName("setupDock")
        self._setup_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable)
        setup_panel = QtPanelView(self.controller, "Setup", panel=self.setup)
        self._setup_dock.setWidget(setup_panel)
        self.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea,
                           self._setup_dock)
        # `toggleViewAction` rather than a button of our own: the check mark
        # and the dock's visibility are then one piece of state, so they
        # cannot disagree after the dock is closed some other way.
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
        self._sync_empty_state()
        self._set_tray_open(False)
        self.show()
        # Nothing is handed focus at boot: a ring on the first control in the
        # tab order (the rail's Setup, the stop) before anything has happened
        # is noise. Tab still reaches every control.
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

    def closeEvent(self, event):
        if not self._closing:
            Dashboard.close(self)
            events.debug("View Closed", "closeEvent", source="QtView")
        for panel in list(self._panels.values()):
            panel.close()
        self._panels.clear()
        self._docks.clear()
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

    # -- the rail: identity, key numbers, the way back, the stop -----------
    def _build_rail(self):
        """The window's heading, set as the *menu widget* so it sits above
        every dock and nothing can cover it or push it off-screen: the stop
        object lives here, at the right-hand end, as the Web view's does.

        Under it, when an error wants acknowledging, the alert band - inside
        the rail, so it can push the docks down but never cover the stop
        (F1). There is no modal for a fault any more.

        It replaced a row of three toolbar tabs that repeated the three dock
        titles under them - two navigations for one set of panels - and a
        sidebar whose only job was to carry the stop at the foot of an empty
        grey column.
        """
        self.rail = QFrame()
        self.rail.setObjectName("rail")
        stack = QVBoxLayout(self.rail)
        stack.setContentsMargins(theme.SPACE[6], theme.PAD,
                                 theme.INSET, theme.PAD)
        stack.setSpacing(theme.GAP)
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE[6])
        stack.addLayout(layout)

        identity = QVBoxLayout()
        identity.setSpacing(0)
        title = QLabel("Transfer stage")
        title.setObjectName("railTitle")
        self.rail_status = ElidedLabel(min_chars=10)
        self.rail_status.setObjectName("caption")
        # The rail's hint: what the stop does and its key, as Tk's stop bar
        # says it (F9).
        # Elided in the middle, so the key itself is what survives.
        self.stop_hint = ElidedLabel(mode=Qt.TextElideMode.ElideMiddle,
                                     min_chars=10)
        self.stop_hint.setObjectName("caption")
        identity.addStretch(1)
        identity.addWidget(title)
        identity.addWidget(self.rail_status)
        identity.addWidget(self.stop_hint)
        identity.addStretch(1)
        layout.addLayout(identity)

        self.rail_readouts = RailReadouts()
        layout.addWidget(self.rail_readouts, 1)

        self._reopen_holder = QWidget()
        self._reopen_holder.setObjectName("bare")
        self._reopen_layout = QHBoxLayout(self._reopen_holder)
        self._reopen_layout.setContentsMargins(0, 0, 0, 0)
        self._reopen_layout.setSpacing(theme.GAP)
        self._reopen_holder.setVisible(False)
        layout.addWidget(self._reopen_holder)

        # The one reopen control for Setup. Its action arrives with the dock
        # when the window opens; until then there is nothing to reopen.
        self.setup_button = QToolButton()
        self.setup_button.setObjectName("ghost")
        self.setup_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setup_button.setVisible(False)
        layout.addWidget(self.setup_button)

        self.stop_button = StopButton()
        self.stop_button.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignVCenter)
        # The stop from anywhere in the application, dialogs included; it only
        # ever stops (F9). The face's tooltip names it.
        self.stop_shortcuts = []
        for sequence in STOP_SHORTCUTS:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(self._on_stop_shortcut)
            self.stop_shortcuts.append(shortcut)
        self.stop_shortcut = self.stop_shortcuts[0]

        stack.addWidget(self._build_alert_band())
        self.setMenuWidget(self.rail)

    def _build_alert_band(self):
        """One line per unacknowledged error, oldest first: a fault mark, the
        word, the message, how many are waiting, and the acknowledgement. The
        errors queue; the next one never overwrites the last (HC-2)."""
        self.alert_band = QFrame()
        self.alert_band.setObjectName("alertBand")
        self.alert_band.setAccessibleName("Errors waiting to be acknowledged")
        row = QHBoxLayout(self.alert_band)
        row.setContentsMargins(0, theme.GAP, 0, 0)
        row.setSpacing(theme.PAD)
        row.addWidget(mark(theme.SEVERITY_MARK["error"]))
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

    def _on_rail_tick(self):
        """The rail's render tick, isolated like a panel's (PYSIDE-10)."""
        try:
            self._sync_stop_button()
            self._sync_rail()
            self._sync_readouts()
        except Exception as exc:
            events.debug("Rail Refresh Failed", str(exc), source="QtView",
                         exception=exc, every=1.0)

    def _sync_rail(self):
        """One readout group per open model, a Reopen group for closed ones,
        and the status line. Rebuilt only when the set of models changes."""
        names = list(self.controller.model_names)
        for name in [n for n in self._rail_groups if n not in names]:
            widget, _ = self._rail_groups.pop(name)
            self.rail_readouts.remove_group(widget)
        for name in names:
            if name not in self._rail_groups:
                self._rail_groups[name] = self._rail_group(name)
                self.rail_readouts.add_group(self._rail_groups[name][0])
        closed = [n for n in self.controller.closed_names if n not in names]
        if closed != self._closed_shown:
            self._closed_shown = closed
            self._build_reopen(closed)
        self.rail_status.set_full_text(self._rail_status_text(names))

    def _rail_status_text(self, names):
        lost = [lost_sentence(n, self._lost.get(n)) for n in names
                if self._lost.get(n)]
        if self.controller.is_estopped:
            return "; ".join(["Stopped"] + lost)
        if lost:
            return "; ".join(lost)
        if names:
            return f"{len(names)} running"
        try:
            scanning = bool(self.setup.state.get("is_scanning"))
        except Exception:
            scanning = False
        return "Scanning ports" if scanning else "Nothing running"

    def _rail_group(self, name):
        """A model's name over its key numbers: caption, value, caption..."""
        try:
            elements = rail_elements(self.controller.schema(name))
        except Exception:
            elements = []
        group = RailGroup(name, elements)
        return group, group.readouts

    def _sync_readouts(self):
        resized = False
        for name, (group, readouts) in list(self._rail_groups.items()):
            try:
                state = self.controller.state(name)
            except Exception:
                continue
            values = state.get("values") or {}
            for element, label in readouts:
                resized |= group.set_value(label, display_text(
                    element, values.get(element["model_attr"], "")))
            if self._show_lost(name, group, lost_devices(state)):
                resized = True
        if resized:
            self.rail_readouts.relayout()
        self.rail_status.set_full_text(
            self._rail_status_text(list(self.controller.model_names)))

    def _show_lost(self, name, group, lost):
        """A lost device, said on the rail and on the dock's own bar."""
        if self._lost.get(name, []) == lost:
            return False
        self._lost[name] = lost
        text = f"{' and '.join(lost)} lost" if lost else ""
        group.set_lost(sentence(text))
        dock = self._docks.get(name)
        if dock is not None:
            dock.set_lost(sentence(text))
        return True

    def _build_reopen(self, closed):
        """A model the operator closed is put away, not gone: the way back is
        on the rail, beside Setup - the other thing that reopens."""
        while self._reopen_layout.count():
            item = self._reopen_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.reopen_buttons = {}
        if closed:
            self._reopen_layout.addWidget(_caption("Reopen"))
        for name in closed:
            button = QPushButton(name)
            button.setObjectName("ghost")
            button.setToolTip(f"Reopen {name}")
            button.clicked.connect(lambda _=False, n=name: self._reopen(n))
            self._reopen_layout.addWidget(button)
            self.reopen_buttons[name] = button
        self._reopen_holder.setVisible(bool(closed))

    def _reopen(self, name):
        """Construct the model again from its remembered config, and bring its
        dock forward when it arrives - the reopen changed nothing on screen
        when the dock landed behind another (AUD-13). A refusal is logged and
        the button stays; it never raises out of a click."""
        if name in self._docks:
            self._bring_forward(self._docks[name])
            return
        self._raise_on_add = name
        try:
            self.open_model(name)
        except Exception as exc:
            self._raise_on_add = None
            events.warn("Reopen Failed", f"{name}: {exc}", source="QtView",
                        exception=exc)
        if name in self._docks:
            self._bring_forward(self._docks[name])
        self._sync_rail()

    @staticmethod
    def _bring_forward(dock):
        dock.show()
        dock.raise_()
        if dock.widget() is not None:
            dock.widget().setFocus(Qt.FocusReason.OtherFocusReason)

    # -- Setup: put away on launch, back from the rail ----------------------
    def _collapse_setup(self):
        """First model launched: the wizard gives way, one click from coming
        back. Called by `base.Dashboard._on_models_changed`.

        Hidden, never destroyed — the panel behind it keeps its scan results
        and its selections, so Refresh and Relaunch do what they did before it
        was put away. The rail's Setup (`toggleViewAction`) brings it back.
        """
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

    # -- the empty state ---------------------------------------------------
    def _build_empty_state(self):
        """What the window says when nothing is running: what to do next.
        It was an empty grey field with a stop button floating in it."""
        self.empty_state = QWidget()
        layout = QVBoxLayout(self.empty_state)
        layout.setContentsMargins(theme.SPACE[6], theme.SPACE[6],
                                  theme.SPACE[6], theme.SPACE[6])
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
        layout.addStretch(1)
        self.setCentralWidget(self.empty_state)

    def _sync_empty_state(self):
        """Shown while no model is open; the way to Setup when it is away.
        Hidden once one is, so the model docks take the whole window."""
        self.empty_state.setVisible(not self._docks)
        setup_away = self._setup_dock is not None and self._setup_dock.isHidden()
        self.empty_setup_button.setVisible(setup_away)

    # -- events: the tray --------------------------------------------------
    def _build_event_tray(self):
        """A bottom tray, not a panel: the latest event on one line, the
        rest a click away. It was a dock as tall as the models, mostly empty.

        The document is capped: an append-forever log in a window that runs a
        whole bench session is an unbounded document, and the operator only
        ever reads the tail of it.
        """
        # The window's status bar, not a dock: it is anchored to the bottom
        # edge by QMainWindow itself, spans the full width, and takes exactly
        # its own height. As a dock with a fixed height it left a blank band
        # under it that the model docks never reclaimed.
        self.event_dock = QStatusBar(self)
        self.event_dock.setObjectName("eventTray")
        self.event_dock.setSizeGripEnabled(False)
        self.tray = QFrame()
        self.tray.setObjectName("tray")
        layout = QVBoxLayout(self.tray)
        layout.setContentsMargins(theme.SPACE[6], theme.GAP,
                                  theme.INSET, theme.GAP)
        layout.setSpacing(theme.GAP)
        head = QHBoxLayout()
        head.setSpacing(theme.PAD)
        self.event_mark = mark(theme.SEVERITY_MARK["info"])
        self._event_mark_colour = theme.SEVERITY_MARK["info"]
        head.addWidget(self.event_mark)
        self.event_latest = ElidedLabel()
        self.event_latest.setObjectName("trayLatest")
        self.event_latest.setProperty("severity", "info")
        self.event_latest.set_full_text("No events yet.")
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
        self.event_dock.addWidget(self.tray, 1)
        self.setStatusBar(self.event_dock)

    def _set_tray_open(self, is_open):
        is_open = bool(is_open)
        self.event_view.setVisible(is_open)
        self.tray_toggle.setText("Hide events" if is_open else "Show events")
        if self.tray_toggle.isChecked() != is_open:
            self.tray_toggle.blockSignals(True)
            self.tray_toggle.setChecked(is_open)
            self.tray_toggle.blockSignals(False)
        self.event_latest.setVisible(not is_open)
        self.event_mark.setVisible(not is_open)
        if is_open:
            self.event_view.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _severity_colour(severity):
        """The ink an event's text is drawn in: legible first (F14). Error is
        ink - signal red text was 3.21:1 on the window, the hardest line to
        read - warning is the trace, information is quiet. The severity's
        colour goes in its mark, beside the word."""
        return theme.SEVERITY_INK.get(severity, theme.MUTED)

    @staticmethod
    def _severity_mark(severity):
        return theme.SEVERITY_MARK.get(severity, theme.MUTED)

    def _show_event(self, event):
        """Everything the log reports, in a tray nobody has to dismiss: a
        mark in the severity's colour, the severity as a word, the line in a
        legible ink - never colour alone (F14, AUD-5)."""
        severity = str(event.severity)
        ink = self._severity_colour(severity)
        label = sentence(severity.upper())
        # Escaped: an event's text can carry a device's reply verbatim, and
        # this widget renders HTML. The mark is a filled cell, not a glyph.
        self.event_view.append(
            f'<span style="background-color:{self._severity_mark(severity)}">'
            f"&nbsp;</span>&nbsp;&nbsp;"
            f'<span style="color:{ink}"><b>{html.escape(label)}</b>&nbsp;&nbsp;'
            f"{html.escape(event.text)}</span>")
        self.event_view.moveCursor(QTextCursor.MoveOperation.End)
        self.event_latest.set_full_text(f"{label}  {event.text}")
        kind = severity if severity in theme.SEVERITY_INK else "info"
        if self.event_latest.property("severity") != kind:
            self.event_latest.setProperty("severity", kind)
            self.event_latest.style().unpolish(self.event_latest)
            self.event_latest.style().polish(self.event_latest)
        colour = self._severity_mark(kind)
        if colour != self._event_mark_colour:
            self._event_mark_colour = colour
            self.event_mark.setStyleSheet(
                f"QFrame#mark {{ background-color: {colour}; border: none; "
                f"border-radius: 1px; }}")

    def _show_popup(self, event):
        """An error that wants acknowledging joins the alert band's queue.

        It was a blocking `QMessageBox.exec()`, one per event: twenty failed
        commands stacked twenty modals over the window, and the stop could
        not be pressed until every one was dismissed (F1, HC-2, UXPM-1).
        `base.Dashboard._on_event` decides *whether* (needs_ack, and never
        while closing); this only shows it.
        """
        if self._closing:
            return
        events.debug("Alert Queued", f"{event.severity}: {event.text}",
                     source="QtView")
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

    @staticmethod
    def _popup_text(event):
        """The whole line - source, title and message. The message alone came
        up blank for an empty-reason fault (HC-11)."""
        text = " ".join(str(getattr(event, "text", "") or
                            event.message or "").split())
        if event.count > 1:
            text += f" (repeated {event.count} times)"
        return text[:5000]

    def _confirm(self, prompt):
        return ask(self, prompt)

    # -- panels ------------------------------------------------------------
    def _add_panel(self, name):
        if name in self._docks:
            return self._docks[name]
        dock = DeviceDock(name, self)
        dock.setObjectName(f"dock:{name}")
        panel = QtPanelView(self.controller, name)
        # A scroll area, so a panel's minimum size never forces the window
        # past the screen - which would carry the rail's stop off it. Safety
        # stays reachable: the stop itself is on the rail.
        scroll = QScrollArea()
        scroll.setObjectName("panelScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(panel)
        dock.setWidget(scroll)
        dock.closed.connect(lambda: self._on_dock_closed(name))
        columns = self._dock_columns()
        if len(columns) >= MAX_DOCK_COLUMNS:
            # A fourth model is a tab in the last column, not a fourth sliver:
            # six side by side were ~100 px each (F22, AUD-7).
            self.tabifyDockWidget(columns[-1][0], dock)
            dock.show()
            dock.raise_()
        elif columns:
            self.splitDockWidget(columns[-1][0], dock, Qt.Orientation.Horizontal)
        else:
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self._last_dock = dock
        self._docks[name] = dock
        self._panels[name] = panel
        if self._lost.get(name):
            dock.set_lost(sentence(f"{' and '.join(self._lost[name])} lost"))
        self._sync_empty_state()
        self._balance_docks()
        self._schedule_balance()
        self._sync_rail()
        if self._raise_on_add == name:
            self._raise_on_add = None
            self._bring_forward(dock)
        events.debug("Dock Opened", name, source="QtView")
        return dock

    def _dock_columns(self):
        """The open docks grouped by where they sit: one list per column, its
        tabs together, in the order they were opened."""
        docks = list(self._docks.values())
        columns, seen = [], set()
        for dock in docks:
            if id(dock) in seen:
                continue
            group = [dock] + [t for t in self.tabifiedDockWidgets(dock)
                              if t in docks and id(t) not in seen]
            seen.update(id(d) for d in group)
            columns.append(group)
        return columns

    def _balance_docks(self):
        """Side-by-side docks in proportion to what each panel needs: Red
        Percent's two columns are twice a probe's one, and an even split
        squeezed it into a scroll while the probe had half a screen of air.
        A column of tabs is sized by the widest panel in it.

        Every column gets at least its share of the window up to
        `MIN_DOCK_PX`, and no column takes the others' floors: a probe beside
        Red Percent was a 178 px sliver at 1000 px (F22)."""
        columns = self._dock_columns()
        if len(columns) < 2:
            return
        names = {id(d): n for n, d in self._docks.items()}
        count = len(columns)
        room = max(self.width(), count)
        floor = min(MIN_DOCK_PX, room // count)
        cap = max(floor, room - floor * (count - 1))
        widths = [min(max(max(self._panels[names[id(d)]].sizeHint().width()
                              for d in column
                              if names.get(id(d)) in self._panels),
                          floor), cap)
                  for column in columns]
        self.resizeDocks([column[0] for column in columns], widths,
                         Qt.Orientation.Horizontal)

    def _schedule_balance(self):
        """Balance once the docks have been laid out: sizes handed to
        `resizeDocks` before that are dropped, which is why the first split
        used to come out the wrong way round."""
        if getattr(self, "_balance_pending", False):
            return
        self._balance_pending = True

        def run():
            self._balance_pending = False
            if not self._closing:
                self._balance_docks()
        QTimer.singleShot(0, run)

    def resizeEvent(self, event):
        """A window made narrower re-shares the columns only when one of them
        has been squeezed under its floor; a split the operator dragged is
        otherwise left alone."""
        super().resizeEvent(event)
        columns = self._dock_columns() if hasattr(self, "_docks") else []
        if len(columns) < 2:
            return
        floor = min(MIN_DOCK_PX, self.width() // len(columns))
        if any(column[0].width() < floor - theme.PAD for column in columns):
            self._schedule_balance()

    def _remove_panel(self, name):
        dock = self._docks.pop(name, None)   # popped first: the dock's own
        panel = self._panels.pop(name, None)  # closeEvent must not re-enter
        self._lost.pop(name, None)
        if dock is None:
            self._sync_rail()
            return
        if self._last_dock is dock:
            remaining = list(self._docks.values())
            # Not None: that made the next dock take the addDockWidget branch
            # and land somewhere else entirely (PYSIDE-16).
            self._last_dock = remaining[-1] if remaining else None
        if panel is not None:
            panel.close()
        dock.close()
        self._sync_empty_state()
        self._schedule_balance()
        self._sync_rail()
        events.debug("Dock Closed", name, source="QtView")

    def _on_dock_closed(self, name):
        """The dock's X. Closing a dock closes the model (there is no hide)."""
        if self._closing or name not in self._docks:
            return
        self.close_model(name)

    # -- the global stop ---------------------------------------------------
    def _on_stop_clicked(self):
        # An open question is answered No first: it never stands between the
        # operator and the stop, and never says Yes for them afterwards.
        cancel_pending_confirms()
        self.toggle_estop_all()
        self._sync_stop_button()

    def _on_stop_shortcut(self):
        """The keyboard's stop: it stops, and does nothing once stopped -
        clearing the latch is a deliberate press of the face (F9)."""
        if self.controller.is_estopped:
            return
        cancel_pending_confirms()
        self.toggle_estop_all()
        self._sync_stop_button()

    def stop_shortcut_text(self):
        return QKeySequence(STOP_SHORTCUT).toString(
            QKeySequence.SequenceFormat.NativeText)

    def _sync_stop_button(self):
        """Face and ring from the Controller, never from the last click."""
        is_estopped = bool(self.controller.is_estopped)
        if self.stop_button.is_latched != is_estopped:
            events.debug("Stop Button", f"latched={is_estopped}",
                         source="QtView")
        self.stop_button.set_latched(is_estopped)
        name = CLEAR_HINT if is_estopped else STOP_HINT
        hint = name if is_estopped else f"{name} ({self.stop_shortcut_text()})"
        tip = (hint if is_estopped else
               f"{hint}. Space or Return presses it when it has focus.")
        if self.stop_button.toolTip() != tip:
            self.stop_button.setToolTip(tip)
            self.stop_button.setAccessibleName(name)
            self.stop_hint.set_full_text(hint)

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
