import sys
"""The PySide6 frontend: one window, one panel renderer, three pure widgets.

Replaces `src/views/pyside/view.py` (1368 lines), `style.qss`, and the PySide
launcher plus its nested setup wizard in `src/app.py`.

What this module is allowed to know
-----------------------------------
The Controller, and — for the Setup panel only — a `Panel` handed to it. No
model import, no `setattr` on a model, no model-specific subclass.
`RedPercentDynamicView`, `PlotDialog` and `ControllerLogWindow` are gone: a
live series is a `plot` element, a saved run is the model's business, and the
gamepad log is a `log_stream` element that all three frontends already show.

Toolkit-neutral logic lives in `station.views.base`; this module supplies
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
per model type instead of six stacked forms (Addendum 2).

Styling
-------
Only through `station.views.theme`. `stylesheet()` generates the whole sheet
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
import shutil

from station import schema as sch
from station.events import events
from station.views import theme
from station.views.base import Dashboard, PanelView

try:                                    # the module imports without PySide6
    from PySide6.QtCore import QEvent, QLocale, QPoint, QRect, Qt, QTimer, Signal
    from PySide6.QtGui import (QColor, QDoubleValidator, QIntValidator, QPainter,
                               QPen, QPixmap, QTextCursor)
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QDockWidget, QFileDialog, QFormLayout, QFrame,
        QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
        QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSizePolicy,
        QTextEdit, QToolBar, QVBoxLayout, QWidget)
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

    QWidget = QMainWindow = QDockWidget = _NoQt


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

# -- geometry. Sizes, not colours and not font sizes: the theme owns those,
# and a widget still has to be told how much room to take. Named here so the
# table's columns, its dropdowns and the docks share one set of numbers
# instead of six literals scattered through the builders.
#: Padding inside a section card, and the gap between two of its lines.
CARD_PAD_PX, ROW_GAP_PX = 12, 8
#: The table's first column - the model's name. Wide enough for the longest.
TABLE_LABEL_MIN_PX = 170
#: Every other table column, so a row with no gamepad still lines up with one
#: that has one.
TABLE_CONTROL_MIN_PX = 150
#: A dropdown sizes to this many characters rather than to its longest option,
#: which is what makes a column of them one width instead of four.
DROPDOWN_CHARS = 14
#: An `indicator` is a lamp: a dot of this size beside its label, never a
#: full-width box. It used to be a `valueLabel` carrying the element's own
#: text, so every indicator on the bench read "Fault    Fault".
LAMP_PX = 16
#: A `log_stream` is a few scrollable lines, fixed. Left to expand, the
#: stepper's Gamepad Log took a third of the panel and pushed Safety - the
#: section that has to be reachable - off the bottom of the dock.
LOG_STREAM_PX = 110
#: The event log's opening height, and the number of lines it keeps. It is a
#: companion to the panels, not the main event; and an append-forever log is
#: an unbounded document in a window meant to run for a whole bench session.
EVENT_LOG_PX, EVENT_LOG_LINES = 150, 500
#: FULL STOP: the one control an operator must be able to hit without looking.
#: The window's global stop and every model's own stop share the metric, and
#: `DANGER_ROLE` is what marks a toggle as one of them (`Model._safety_section`
#: declares the stop with `on_role`/`off_role` both "danger").
STOP_BUTTON_PX, STOP_FONT_SCALE = 54, 1.3
DANGER_ROLE = "danger"


# ---------------------------------------------------------------------------
# Pure functions. No Qt, no widgets, unit-tested without a QApplication.
# ---------------------------------------------------------------------------

def _rule(selector, declarations):
    body = "".join(f"    {name}: {value};\n" for name, value in declarations.items())
    return f"{selector} {{\n{body}}}\n"


def stylesheet():
    """The whole Qt stylesheet, generated from `station.views.theme`.

    Every colour and every font size in this string comes from a theme value.
    That is the entire reason `style.qss` is deleted rather than ported: a
    checked-in sheet is a second palette that drifts from the other two views
    and cannot follow `--font-size` at launch.
    """
    base_font, base_size, _ = theme.font()
    _, title_size, _ = theme.font(1.15, bold=True)
    _, header_size, _ = theme.font(0.85, bold=True)
    neutral_bg, neutral_fg = theme.colors("neutral")
    info_bg, _ = theme.colors("info")
    danger_bg, _ = theme.colors("danger")
    disabled_bg, disabled_fg = theme.DISABLED

    sheet = [
        _rule("QWidget", {"background-color": theme.BACKGROUND,
                          "color": theme.TEXT,
                          "font-family": base_font,
                          "font-size": f"{base_size}pt"}),
        _rule("QMainWindow, QDialog, QMessageBox",
              {"background-color": theme.BACKGROUND}),
        # A label paints no rectangle of its own: without this every caption
        # inside a card stamped a BACKGROUND patch over the card's SURFACE.
        _rule("QLabel", {"background": "transparent"}),
        _rule("QFrame#card", {"background-color": theme.SURFACE,
                              "border": f"1px solid {neutral_bg}",
                              "border-radius": "6px"}),
        _rule("QLabel#sectionTitle", {"color": theme.TEXT,
                                      "font-size": f"{title_size}pt",
                                      "font-weight": "600"}),
        # The table's furniture: a column caption is quiet, a row's own name
        # is not, so a Setup row reads as "Stepper Probe: this port" rather
        # than as four equally loud words.
        _rule("QLabel#columnHeader", {"color": theme.MUTED,
                                      "font-size": f"{header_size}pt",
                                      "font-weight": "600",
                                      "padding-bottom": "2px"}),
        _rule("QLabel#rowTitle", {"color": theme.TEXT, "font-weight": "600",
                                  "padding-right": "12px"}),
        # A readout is bold text straight on the card; an entry is a bordered
        # well sunk to the window colour. That is the whole distinction, and
        # it survives any palette because it is shape, not hue.
        _rule("QLabel#valueLabel", {"color": theme.TEXT, "font-weight": "600",
                                    "padding": "4px 2px"}),
        _rule("QLabel#statusLabel", {"color": danger_bg, "padding": "2px 4px"}),
        _rule("QLabel#staleLabel", {"color": theme.MUTED, "padding": "2px 4px"}),
        # `_set_stale` sets this property on the panel; every readout dims at
        # once, so a frozen value can never read as a live one.
        _rule('QWidget[stale="true"] QLabel#valueLabel', {"color": theme.MUTED}),
        _rule("QLineEdit, QComboBox, QTextEdit",
              {"background-color": theme.BACKGROUND, "color": theme.TEXT,
               "border": f"1px solid {neutral_bg}", "border-radius": "4px",
               "padding": "5px 6px"}),
        _rule("QLineEdit:focus, QComboBox:focus",
              {"border": f"1px solid {info_bg}"}),
        _rule("QPushButton", {"background-color": neutral_bg,
                              "color": neutral_fg, "border": "none",
                              "border-radius": "4px", "padding": "6px 12px",
                              "font-weight": "600"}),
        _rule("QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled",
              {"background-color": disabled_bg, "color": disabled_fg}),
        _rule("QListWidget", {"background-color": theme.BACKGROUND,
                              "border": "none"}),
        _rule("QListWidget::item", {"padding": "6px"}),
        _rule("QDockWidget", {"color": theme.TEXT, "font-weight": "600"}),
        # A dock title is a heading, not a caption: the operator finds a panel
        # by reading these.
        _rule("QDockWidget::title", {"background": theme.SURFACE,
                                     "color": theme.TEXT,
                                     "font-size": f"{title_size}pt",
                                     "border-bottom": f"1px solid {neutral_bg}",
                                     "padding": "8px 10px"}),
        # The toolbar is where a hidden dock comes back from, so a checked
        # action has to read as pressed rather than as merely hovered.
        _rule("QToolBar", {"background": theme.SURFACE, "border": "none",
                           "padding": "4px", "spacing": "6px"}),
        _rule("QToolBar QToolButton", {"background": "transparent",
                                       "color": theme.MUTED,
                                       "border": "none",
                                       "border-bottom": "2px solid transparent",
                                       "padding": "6px 12px"}),
        _rule("QToolBar QToolButton:hover", {"background": neutral_bg,
                                             "color": theme.TEXT}),
        # A shown dock reads as a selected tab rather than as a filled button:
        # all three are usually shown, and three solid blocks of colour at the
        # top of the window is not a heading, it is noise.
        _rule("QToolBar QToolButton:checked",
              {"color": theme.TEXT, "border-bottom": f"2px solid {info_bg}"}),
    ]
    for role, (background, foreground) in theme.ROLES.items():
        sheet.append(_rule(f'QPushButton[role="{role}"]',
                           {"background-color": background,
                            "color": foreground}))
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
            self.form.addRow(QLabel(label), widget)
        else:
            self.form.addRow(widget)

    def add_wide(self, label, widget):
        """A control too tall or too wide to sit beside its caption."""
        if label:
            self.form.addRow(QLabel(label))
        self.form.addRow(widget)


class PanelTable:
    """The shared grid that every `layout="row"` section adds one line to.

    Columns are allocated **by label**, not by position, so "Port" sits under
    "Port" in every row even in a row that declares no gamepad and no port at
    all. That is what makes the Setup panel read as a table — one line per
    model, aligned — instead of as six ragged lines of different lengths,
    which is what "everything in one vertical tab is poor UI/UX" was about.

    Column 0 is the row's own name; the first grid row is the header.
    """

    HEADER_ROW = 0

    def __init__(self, grid):
        self.grid = grid
        self.columns = {}        # label -> column index
        self.rows = 0
        self._next_column = 1    # column 0 belongs to the row titles

    def add_row(self, title):
        self.rows += 1
        label = QLabel(title)
        label.setObjectName("rowTitle")
        self.grid.addWidget(label, self.rows, 0)
        return TableRow(self, self.rows)

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
        self.grid.setColumnStretch(column, 1)
        if header:
            caption = QLabel(header.rstrip(":"))
            caption.setObjectName("columnHeader")
            self.grid.addWidget(caption, self.HEADER_ROW, column)
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
        pen = QPen(QColor(theme.colors("danger")[0]))
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


class DeviceDock(QDockWidget):
    """One model's panel, in a closable dock. Closing it closes the model."""

    closed = Signal()

    def __init__(self, title, parent=None):
        QDockWidget.__init__(self, title, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)

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
        self._layout.setContentsMargins(CARD_PAD_PX, CARD_PAD_PX,
                                        CARD_PAD_PX, CARD_PAD_PX)
        self._layout.setSpacing(ROW_GAP_PX)
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.stale_label = QLabel("readings are stale")
        self.stale_label.setObjectName("staleLabel")
        self.stale_label.setVisible(False)

        self._build()

        self._layout.addStretch()
        self._layout.addWidget(self.stale_label)
        self._layout.addWidget(self.status_label)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start(self.REFRESH_MS)
        events.debug("Panel Opened", f"{self.name} rendered "
                     f"{len(self._elements)} elements", source="QtView")

    # -- lifecycle ---------------------------------------------------------
    def close(self):
        """Stop the render tick, drop the elements, close the widget.

        Both bases define `close`, so neither is left to the MRO. It closes
        no model and stops no device loop: what a *view* owns is its tick.
        """
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
        if layout == "row":
            return self._panel_table().add_row(title)
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
        form = QFormLayout(card)
        form.setContentsMargins(CARD_PAD_PX, CARD_PAD_PX, CARD_PAD_PX,
                                CARD_PAD_PX)
        form.setHorizontalSpacing(CARD_PAD_PX)
        form.setVerticalSpacing(ROW_GAP_PX)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        form.addRow(label)
        self._layout.addWidget(card)
        return ColumnSection(form)

    def _panel_table(self):
        """One table per panel, built where its first row section appears.

        Every later row section joins it rather than starting a card of its
        own, which is the only way their columns can line up.
        """
        if self._table is None:
            card = QFrame()
            card.setObjectName("card")
            card.setSizePolicy(QSizePolicy.Policy.Preferred,
                               QSizePolicy.Policy.Maximum)
            grid = QGridLayout(card)
            grid.setContentsMargins(CARD_PAD_PX, CARD_PAD_PX, CARD_PAD_PX,
                                    CARD_PAD_PX)
            grid.setHorizontalSpacing(CARD_PAD_PX)
            grid.setVerticalSpacing(ROW_GAP_PX)
            grid.setColumnMinimumWidth(0, TABLE_LABEL_MIN_PX)
            grid.setColumnStretch(0, 0)
            self._layout.addWidget(card)
            self._table = PanelTable(grid)
        return self._table

    # -- element builders --------------------------------------------------
    def _make_readonly(self, container, element):
        value = QLabel("")
        value.setObjectName("valueLabel")
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
        button = QPushButton(element.get("text", ""))
        button.setProperty("role", element.get("role", "neutral"))
        # Values travel with the command (`_gather_inputs` reads the widgets),
        # so PYSIDE-5's "the click read the previous value" cannot recur and
        # no focus is forced anywhere - the named Tk anti-fix.
        button.clicked.connect(lambda: self._run(element))
        self._remember(element, button)
        container.add_wide("", button)

    def _make_toggle(self, container, element):
        button = QPushButton(element.get("false_text", "Off"))
        button.clicked.connect(lambda: self._run_toggle(element))
        self._remember(element, button)
        if DANGER_ROLE in (element.get("on_role"), element.get("off_role")):
            # A model's FULL STOP takes the section's whole width and the same
            # tall metric as the window's global one. It rendered as a caption
            # and a small button beside it - "FULL STOP    FULL STOP" - which
            # is neither prominent nor even readable as one control.
            button.setMinimumHeight(STOP_BUTTON_PX)
            button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
            container.add_wide("", button)
            return
        container.add(element.get("text", ""), button)

    def _make_dropdown(self, container, element):
        combo = QComboBox()
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
        refresh = QPushButton("↺")
        refresh.setToolTip("Rescan the available choices")
        refresh.setFixedWidth(32)
        refresh.clicked.connect(lambda: self._reload_options(element, combo))
        cell = self._row(combo, refresh)
        if container.control_width:
            cell.setMinimumWidth(container.control_width)
        container.add(element.get("text", ""), cell)

    def _make_region_select(self, container, element):
        button = QPushButton(element.get("text", "Select region"))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._pick_region(element))
        value = QLabel(sch.format_region(None))
        value.setObjectName("valueLabel")
        self._remember(element, value)
        container.add("", self._row(button, value))

    def _make_file_save(self, container, element):
        button = QPushButton(element.get("text", "Save"))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._save_to_file(element))
        self._remember(element, button)
        container.add_wide("", button)

    def _make_file_open(self, container, element):
        button = QPushButton(element.get("text", "Open"))
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
        lamp.setFixedSize(LAMP_PX, LAMP_PX)
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
        if widget.text() != text:
            widget.setText(text)
        self._clean_text[id(element)] = text

    def _set_on(self, element, is_on):
        widget = self._widget_for(element)
        if widget is None:
            return
        colours = theme.toggle_colors(element, is_on)
        if element["type"] == "toggle":
            widget.setStyleSheet(
                f"background-color: {colours['background']}; "
                f"color: {colours['foreground']}; "
                f"border: 2px solid {colours['border']}; border-radius: 4px;")
            wanted = element.get("true_text" if is_on else "false_text", "")
            if widget.text() != wanted:
                widget.setText(wanted)
            return
        # An indicator: a filled dot when on, the same colour outlined when
        # off, and no text at all. Writing `element["text"]` here is what made
        # every indicator read its own caption twice.
        widget.setStyleSheet(
            f"background-color: {colours['background']}; "
            f"border: 2px solid {colours['border']}; "
            f"border-radius: {LAMP_PX // 2}px;")

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
        if self.property("stale") == ("true" if is_stale else "false"):
            return
        self.setProperty("stale", "true" if is_stale else "false")
        self.stale_label.setVisible(bool(is_stale))
        self.style().unpolish(self)
        self.style().polish(self)

    def _confirm(self, prompt):
        answer = QMessageBox.question(
            self, "Confirm", prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def _show_refused(self, reason):
        """A refusal is a status line, not a dialog.

        Both desktop views used to drop `Refused` on the floor entirely; the
        answer is not a modal per refusal but one non-modal line the operator
        can read and act on.
        """
        if self.status_label.text() != (reason or ""):
            self.status_label.setText(reason or "")
        if reason:
            events.debug("Refused Shown", f"{self.name}: {reason}",
                         source="QtView")

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
            if current in options:
                combo.setCurrentText(current)
        finally:
            combo.blockSignals(False)

    def _set_combo_text(self, combo, text):
        """`current_text` returns "" for an absent value, and "" must never
        become a selectable option named "None" (schema v2's note)."""
        if not text or combo.currentText() == text:
            return
        combo.blockSignals(True)
        try:
            if combo.findText(text) < 0:
                combo.addItem(text)
            combo.setCurrentText(text)
        finally:
            combo.blockSignals(False)

    # -- plumbing ----------------------------------------------------------
    def _remember(self, element, widget):
        self._widgets[id(element)] = widget

    def _widget_for(self, element):
        return self._widgets.get(id(element))

    @staticmethod
    def _row(*widgets):
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            layout.addWidget(widget)
        return holder


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

class QtDashboard(Dashboard, QMainWindow):
    """The dashboard window: Setup first, then one dock per open model.

    Absorbs `DashboardWindow`, `QtEventLogPanel` and `QtErrorPopupManager`.
    """

    #: Every cross-thread hop goes through this, queued. See the module
    #: docstring: AutoConnection is what PYSIDE-21 was.
    _marshalled = Signal(object)

    STOP_REFRESH_MS = 250

    def __init__(self, controller, setup):
        QMainWindow.__init__(self)
        Dashboard.__init__(self, controller, setup)
        self._docks = {}
        self._last_dock = None
        self._is_focused = True
        self._setup_dock = None
        self.setup_action = None

        self.setWindowTitle("Transfer Stage")
        self.resize(1200, 800)
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks
                            | QMainWindow.DockOption.AllowTabbedDocks)
        self._marshalled.connect(self._on_marshalled, Qt.QueuedConnection)

        self._build_toolbar()
        self._build_sidebar_dock()
        self._build_event_panel()

        self._stop_timer = QTimer(self)
        self._stop_timer.timeout.connect(self._sync_stop_button)
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
        # dock that cannot be closed, so without it the toolbar entry is dead.
        self._setup_dock = QDockWidget("Setup", self)
        self._setup_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable)
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
        existing = self.toolbar.actions()
        self.toolbar.insertAction(existing[0] if existing else None,
                                  self.setup_action)

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
        self._build_sidebar()
        self._sync_stop_button()
        self.resizeDocks([self.event_dock], [EVENT_LOG_PX],
                         Qt.Orientation.Vertical)
        self.show()
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
        for dock in list(self._docks.values()):
            widget = dock.widget()
            if isinstance(widget, QtPanelView):
                widget.close()
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

    # -- the toolbar: where a hidden dock comes back from -------------------
    def _build_toolbar(self):
        """One toolbar, holding a checkable entry per hideable dock.

        Setup's entry is added by `QtDashboard.open`, where the dock exists,
        and is put
        first: it is the one an operator reaches for again mid-session.
        """
        self.toolbar = QToolBar("View", self)
        self.toolbar.setObjectName("viewToolBar")
        self.toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)

    def _collapse_setup(self):
        """First model launched: the wizard gives way, one click from coming
        back. Called by `base.Dashboard._on_models_changed`.

        Hidden, never destroyed — the panel behind it keeps its scan results
        and its selections, so Refresh and Relaunch do what they did before it
        was put away. `toggleViewAction` is what brings it back.
        """
        if self._setup_dock is None or self._setup_dock.isHidden():
            return
        self._setup_dock.hide()
        events.debug("Setup Collapsed", "the Setup dock is hidden; the "
                     "toolbar's Setup entry brings it back", source="QtView")

    def show_setup(self):
        """Bring Setup back for a re-scan or a relaunch (so does the toolbar)."""
        if self._setup_dock is None:
            return
        self._setup_dock.show()
        self._setup_dock.raise_()
        events.debug("Setup Reopened", "the Setup dock is visible again",
                     source="QtView")

    # -- events ------------------------------------------------------------
    def _build_event_panel(self):
        """A companion strip along the bottom, not the main event.

        The document is capped: an append-forever log in a window that runs a
        whole bench session is an unbounded document, and the operator only
        ever reads the tail of it.
        """
        self.event_dock = QDockWidget("Event Log", self)
        self.event_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.event_view = QTextEdit()
        self.event_view.setReadOnly(True)
        self.event_view.document().setMaximumBlockCount(EVENT_LOG_LINES)
        self.event_view.setMinimumHeight(EVENT_LOG_PX // 2)
        self.event_dock.setWidget(self.event_view)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,
                           self.event_dock)
        log_action = self.event_dock.toggleViewAction()
        log_action.setText("Event Log")
        self.toolbar.addAction(log_action)

    def _show_event(self, event):
        """Everything the log reports, in a panel nobody has to dismiss."""
        role = theme.SEVERITY_ROLE.get(event.severity, "neutral")
        colour = theme.colors(role)[0]
        # Escaped: an event's text can carry a device's reply verbatim, and
        # this widget renders HTML.
        self.event_view.append(
            f'<span style="color:{colour}">[{html.escape(event.severity.upper())}]'
            f"</span> {html.escape(event.text)}")
        self.event_view.moveCursor(QTextCursor.MoveOperation.End)

    def _show_popup(self, event):
        """The one acknowledged modal, parented to this window.

        `base.Dashboard._on_event` decides *whether* (needs_ack, and never
        while closing); this only shows it.
        """
        if self._closing:
            return
        events.debug("Popup Shown", f"{event.severity}: {event.text}",
                     source="QtView")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(event.title)
        box.setText(self._popup_text(event))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    @staticmethod
    def _popup_text(event):
        text = event.message or ""
        if event.count > 1:
            text += f"\n\n(repeated {event.count} times)"
        return text[:5000]

    def _confirm(self, prompt):
        answer = QMessageBox.question(
            self, "Confirm", prompt,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    # -- panels ------------------------------------------------------------
    def _add_panel(self, name):
        if name in self._docks:
            return self._docks[name]
        dock = DeviceDock(name, self)
        dock.setWidget(QtPanelView(self.controller, name))
        dock.closed.connect(lambda: self._on_dock_closed(name))
        if self._last_dock is not None:
            self.splitDockWidget(self._last_dock, dock, Qt.Orientation.Horizontal)
        else:
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self._last_dock = dock
        self._docks[name] = dock
        self._build_sidebar()
        events.debug("Dock Opened", name, source="QtView")
        return dock

    def _remove_panel(self, name):
        dock = self._docks.pop(name, None)   # popped first: the dock's own
        if dock is None:                     # closeEvent must not re-enter
            self._build_sidebar()
            return
        if self._last_dock is dock:
            remaining = list(self._docks.values())
            # Not None: that made the next dock take the addDockWidget branch
            # and land somewhere else entirely (PYSIDE-16).
            self._last_dock = remaining[-1] if remaining else None
        widget = dock.widget()
        if isinstance(widget, QtPanelView):
            widget.close()
        dock.close()
        self._build_sidebar()
        events.debug("Dock Closed", name, source="QtView")

    def _on_dock_closed(self, name):
        """The dock's X. Closing a dock closes the model (there is no hide)."""
        if self._closing or name not in self._docks:
            return
        self.close_model(name)

    # -- sidebar -----------------------------------------------------------
    def _build_sidebar_dock(self):
        self.sidebar = QDockWidget("Models", self)
        self.sidebar.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.sidebar.setMinimumWidth(220)

        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(ROW_GAP_PX, ROW_GAP_PX, ROW_GAP_PX,
                                  ROW_GAP_PX)
        layout.setSpacing(ROW_GAP_PX)
        self.model_list = QListWidget()
        self.model_list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.model_list)

        # Bottom, not top: directly above the checkboxes that close models it
        # was an easy accidental-click target when reaching for something else
        # (PYSIDE-16; the Tk view puts it at the bottom for the same reason).
        # Tall and full width, because it is the one control that has to be
        # hittable without looking for it.
        self.stop_button = QPushButton("FULL STOP")
        self.stop_button.setMinimumHeight(STOP_BUTTON_PX)
        self.stop_button.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Fixed)
        self.stop_button.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self.stop_button)

        self.sidebar.setWidget(holder)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sidebar)
        models_action = self.sidebar.toggleViewAction()
        models_action.setText("Models")
        self.toolbar.addAction(models_action)

    def _build_sidebar(self):
        """Open models, checked; closed ones, unchecked. Checking reopens.

        The list is the Controller's, not a fourth hardcoded copy of the
        device names - the sidebar could offer a model the builder had never
        heard of.
        """
        open_names = list(self.controller.model_names)
        closed_names = [n for n in self.controller.closed_names
                        if n not in open_names]
        self.model_list.blockSignals(True)
        try:
            self.model_list.clear()
            for name in open_names + closed_names:
                item = QListWidgetItem(name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if name in open_names
                                   else Qt.CheckState.Unchecked)
                self.model_list.addItem(item)
        finally:
            self.model_list.blockSignals(False)

    def _on_item_changed(self, item):
        name = item.text()
        wants_open = item.checkState() == Qt.CheckState.Checked
        if wants_open and name not in self._docks:
            try:
                self.open_model(name)
            except Exception as exc:
                events.warn("Reopen Failed", f"{name}: {exc}", source="QtView",
                            exception=exc)
                self._set_sidebar_checked(name, False)
        elif not wants_open and name in self._docks:
            self.close_model(name)

    def _set_sidebar_checked(self, name, is_checked):
        """Set a checkbox without re-entering `_on_item_changed`."""
        self.model_list.blockSignals(True)
        try:
            for index in range(self.model_list.count()):
                item = self.model_list.item(index)
                if item.text() == name:
                    item.setCheckState(Qt.CheckState.Checked if is_checked
                                       else Qt.CheckState.Unchecked)
                    break
        finally:
            self.model_list.blockSignals(False)

    # -- the global stop ---------------------------------------------------
    def _on_stop_clicked(self):
        self.toggle_estop_all()
        self._sync_stop_button()

    def _sync_stop_button(self):
        """Label and colour from the Controller, never from the last click."""
        is_estopped = self.controller.is_estopped
        colours = theme.toggle_colors({"on_role": "danger",
                                       "off_role": "danger"}, is_estopped)
        wanted = "CLEAR FULL STOP" if is_estopped else "FULL STOP"
        if self.stop_button.text() != wanted:
            self.stop_button.setText(wanted)
            events.debug("Stop Button", f"latched={is_estopped}",
                         source="QtView")
        _, size, _ = theme.font(STOP_FONT_SCALE, bold=True)
        self.stop_button.setStyleSheet(
            f"background-color: {colours['background']}; "
            f"color: {colours['foreground']}; "
            f"border: 3px solid {colours['border']}; "
            f"border-radius: 6px; padding: 12px; "
            f"font-size: {size}pt; font-weight: bold;")

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
