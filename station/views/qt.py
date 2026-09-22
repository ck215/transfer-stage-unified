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
import os
import shutil

from station import schema as sch
from station.events import events
from station.views import theme
from station.views.base import Dashboard, PanelView

try:                                    # the module imports without PySide6
    from PySide6.QtCore import QEvent, QLocale, QPoint, QRect, Qt, QTimer, Signal
    from PySide6.QtGui import (QColor, QDoubleValidator, QPainter, QPen,
                               QPixmap, QTextCursor)
    from PySide6.QtWidgets import (
        QApplication, QComboBox, QDockWidget, QFileDialog, QFormLayout, QFrame,
        QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
        QMainWindow, QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget)
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
        _rule("QFrame#card", {"background-color": theme.SURFACE,
                              "border": f"1px solid {neutral_bg}",
                              "border-radius": "6px"}),
        _rule("QLabel#sectionTitle", {"color": theme.TEXT,
                                      "font-size": f"{title_size}pt",
                                      "font-weight": "600"}),
        _rule("QLabel#valueLabel", {"color": theme.TEXT, "font-weight": "600"}),
        _rule("QLabel#statusLabel", {"color": danger_bg}),
        _rule("QLabel#staleLabel", {"color": theme.MUTED}),
        # `_set_stale` sets this property on the panel; every readout dims at
        # once, so a frozen value can never read as a live one.
        _rule('QWidget[stale="true"] QLabel#valueLabel', {"color": theme.MUTED}),
        _rule("QLineEdit, QComboBox, QTextEdit",
              {"background-color": theme.SURFACE, "color": theme.TEXT,
               "border": f"1px solid {neutral_bg}", "border-radius": "4px",
               "padding": "4px"}),
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
        _rule("QDockWidget::title", {"background": theme.SURFACE,
                                     "padding": "6px"}),
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

        self._layout = QVBoxLayout(self)
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

    # -- element builders --------------------------------------------------
    def _make_section(self, title):
        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        form.addRow(label)
        self._layout.addWidget(card)
        return form

    def _make_readonly(self, container, element):
        value = QLabel("")
        value.setObjectName("valueLabel")
        self._remember(element, value)
        container.addRow(QLabel(element.get("text", "")), value)

    def _make_entry(self, container, element):
        entry = QLineEdit()
        bounds = validator_bounds(element)
        if bounds is not None:
            low, high, decimals = bounds
            validator = QDoubleValidator(low, high, decimals, entry)
            # PYSIDE-19: the C locale, always. A comma-decimal system locale
            # rejected "." outright, and the operator could not type a number.
            validator.setLocale(QLocale.c())
            entry.setValidator(validator)
        self._remember(element, entry)
        container.addRow(QLabel(element.get("text", "")), entry)

    def _make_button(self, container, element):
        button = QPushButton(element.get("text", ""))
        button.setProperty("role", element.get("role", "neutral"))
        # Values travel with the command (`_gather_inputs` reads the widgets),
        # so PYSIDE-5's "the click read the previous value" cannot recur and
        # no focus is forced anywhere - the named Tk anti-fix.
        button.clicked.connect(lambda: self._run(element))
        self._remember(element, button)
        container.addRow(button)

    def _make_toggle(self, container, element):
        button = QPushButton(element.get("false_text", "Off"))
        button.clicked.connect(lambda: self._run_toggle(element))
        self._remember(element, button)
        container.addRow(QLabel(element.get("text", "")), button)

    def _make_dropdown(self, container, element):
        combo = QComboBox()
        self._remember(element, combo)
        self._reload_options(element, combo)
        combo.currentTextChanged.connect(
            lambda text: self._on_dropdown_changed(element, text))
        refresh = QPushButton("↺")
        refresh.setToolTip("Rescan the available choices")
        refresh.setFixedWidth(32)
        refresh.clicked.connect(lambda: self._reload_options(element, combo))
        container.addRow(QLabel(element.get("text", "")),
                         self._row(combo, refresh))

    def _make_region_select(self, container, element):
        button = QPushButton(element.get("text", "Select region"))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._pick_region(element))
        value = QLabel(sch.format_region(None))
        value.setObjectName("valueLabel")
        self._remember(element, value)
        container.addRow(button, value)

    def _make_file_save(self, container, element):
        button = QPushButton(element.get("text", "Save"))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._save_to_file(element))
        self._remember(element, button)
        container.addRow(button)

    def _make_file_open(self, container, element):
        button = QPushButton(element.get("text", "Open"))
        button.setProperty("role", element.get("role", "neutral"))
        button.clicked.connect(lambda: self._open_from_file(element))
        self._remember(element, button)
        container.addRow(button)

    def _make_plot(self, container, element):
        plot = SeriesPlot(role=element.get("role", "neutral"))
        plot.setToolTip(f"{element.get('x_label', '')} / "
                        f"{element.get('y_label', '')}".strip(" /"))
        self._remember(element, plot)
        container.addRow(QLabel(element.get("text", "")))
        container.addRow(plot)

    def _make_image(self, container, element):
        label = QLabel("")
        label.setObjectName("valueLabel")
        label.setMinimumHeight(140)
        self._remember(element, label)
        container.addRow(QLabel(element.get("text", "")))
        container.addRow(label)

    def _make_indicator(self, container, element):
        lamp = QLabel("")
        lamp.setObjectName("valueLabel")
        self._remember(element, lamp)
        container.addRow(QLabel(element.get("text", "")), lamp)

    def _make_log_stream(self, container, element):
        view = QTextEdit()
        view.setReadOnly(True)
        view.setMinimumHeight(140)
        self._remember(element, view)
        container.addRow(QLabel(element.get("text", "")))
        container.addRow(view)

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
        text = "" if text is None else str(text)
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
        widget.setStyleSheet(
            f"background-color: {colours['background']}; "
            f"color: {colours['foreground']}; "
            f"border: 2px solid {colours['border']}; border-radius: 4px;")
        if element["type"] == "toggle":
            wanted = element.get("true_text" if is_on else "false_text", "")
            if widget.text() != wanted:
                widget.setText(wanted)
        else:
            widget.setText(element.get("text", ""))

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

        self.setWindowTitle("Transfer Stage")
        self.resize(1200, 800)
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks
                            | QMainWindow.DockOption.AllowTabbedDocks)
        self._marshalled.connect(self._on_marshalled, Qt.QueuedConnection)

        self._build_sidebar_dock()
        self._build_event_panel()

        self._stop_timer = QTimer(self)
        self._stop_timer.timeout.connect(self._sync_stop_button)
        self._stop_timer.start(self.STOP_REFRESH_MS)

    # -- lifecycle ---------------------------------------------------------
    def open(self):
        """Subscribe, show the Setup panel, show the window.

        Setup is a `Panel` the Controller does not own, so it is passed to the
        panel view directly. There is no separate wizard window and no second
        copy of the device list: one setup, rendered like everything else.
        """
        Dashboard.open(self)
        self._setup_dock = DeviceDock("Setup", self)
        self._setup_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        setup_panel = QtPanelView(self.controller, "Setup", panel=self.setup)
        self._setup_dock.setWidget(setup_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,
                           self._setup_dock)

        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.close)

        for name in self.controller.model_names:
            self._add_panel(name)
        self._build_sidebar()
        self._sync_stop_button()
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

    # -- events ------------------------------------------------------------
    def _build_event_panel(self):
        self.event_dock = QDockWidget("Event Log", self)
        self.event_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.event_view = QTextEdit()
        self.event_view.setReadOnly(True)
        self.event_dock.setWidget(self.event_view)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea,
                           self.event_dock)

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
        self.model_list = QListWidget()
        self.model_list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.model_list)

        # Bottom, not top: directly above the checkboxes that close models it
        # was an easy accidental-click target when reaching for something else
        # (PYSIDE-16; the Tk view puts it at the bottom for the same reason).
        self.stop_button = QPushButton("FULL STOP")
        self.stop_button.clicked.connect(self._on_stop_clicked)
        layout.addWidget(self.stop_button)

        self.sidebar.setWidget(holder)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sidebar)

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
        self.stop_button.setStyleSheet(
            f"background-color: {colours['background']}; "
            f"color: {colours['foreground']}; "
            f"border: 2px solid {colours['border']}; "
            f"border-radius: 4px; padding: 10px; font-weight: bold;")

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
