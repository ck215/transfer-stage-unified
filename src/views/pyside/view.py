import sys
import os
import traceback
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QDockWidget, QListWidget, QWidget,
    QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QFrame,
    QMessageBox, QListWidgetItem, QDialog, QFileDialog, QFormLayout,
    QComboBox, QTextEdit
)
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QEvent
from PySide6.QtGui import QPainter, QColor, QPen, QDoubleValidator, QTextCursor

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT

from model import schema as sch
from model import devices
from error_routing import ErrorRouter


class QtErrorPopupManager(QObject):
    """Qt subscriber on the event bus (RC-8 item 3).

    What changed at S11. This used to own three process-global callbacks on
    `ErrorRouter` and open a **modal** `QMessageBox` for every severity,
    including info. That is TEMP-12 — "Temperature Send" raising a dialog on
    every send — and it is also what hung the entire Qt test suite for three
    sessions: a modal with nobody to click it.

    Now it subscribes, and the rule is narrow: an event goes to the non-modal
    log panel, and **only** an `error` that sets `requires_ack` additionally
    raises a modal. Nothing quieter can, because `EventBus.publish` refuses
    `requires_ack` on anything but an error.
    """

    _instance = None
    _message_signal = Signal(object)  # Event, marshalled to the GUI thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self._message_signal.connect(self._on_event)
        self._log = []
        self._panel = None
        ErrorRouter.subscribe(self._publish)

    @classmethod
    def initialize(cls, parent=None):
        if cls._instance is None:
            cls._instance = QtErrorPopupManager(parent)
        return cls._instance

    @classmethod
    def shutdown(cls):
        """Unsubscribe and forget the instance. Without this a relaunch in
        the same process stacks a second subscriber on the bus and every
        event is handled twice."""
        if cls._instance is not None:
            ErrorRouter.unsubscribe(cls._instance._publish)
            cls._instance = None

    def _publish(self, event):
        """Bus callback. Runs on whatever thread published, so it does
        nothing but hand the event to the GUI thread through a signal."""
        self._message_signal.emit(event)

    def _on_event(self, event):
        self._log.append(event)
        if len(self._log) > 500:
            del self._log[:len(self._log) - 500]
        if self._panel is not None:
            self._panel.append_event(event)
        if event.severity == "error" and event.requires_ack:
            QMessageBox.critical(None, event.title, self._format(event))

    @staticmethod
    def _format(event):
        # `or ""` is not decoration: a `None` message used to make this
        # method raise `TypeError` while formatting an error, which destroys
        # the only report of that error. There is a test for it.
        text = event.message or ""
        if event.exception is not None:
            try:
                text += (f"\n\nDetails:\n{type(event.exception).__name__}: "
                         f"{event.exception}")
            except Exception:
                text += "\n\nDetails: <Unprintable Exception>"
        if event.count > 1:
            text += f"\n\n(repeated {event.count} times)"
        if len(text) > 5000:
            text = text[:5000] + "... [TRUNCATED]"
        return text

    def attach_panel(self, panel):
        """Bind a non-modal log widget and replay what it missed."""
        self._panel = panel
        for event in self._log:
            panel.append_event(event)

    def events(self):
        return list(self._log)

    @classmethod
    def setup_excepthook(cls, tk_root=None):
        """Kept as the name `app.py` calls; the work is shared now.

        Each view used to install its own `sys.excepthook` and nothing else,
        so an exception in a poller thread never reached a human
        (ERRORS-4, PYSIDE-15).
        """
        from error_routing import install_exception_hooks
        install_exception_hooks(tk_root=tk_root)


class QtEventLogPanel(QDockWidget):
    """The non-modal half of RC-8 item 3: everything the bus reports, in a
    panel the operator can leave open, instead of a dialog per event."""

    _COLOURS = {"info": "#8fa3b0", "warning": "#d6a13a", "error": "#d64545"}

    def __init__(self, parent=None):
        super().__init__("Event Log", parent)
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self.setWidget(self._text)

    def append_event(self, event):
        colour = self._COLOURS.get(event.severity, "#8fa3b0")
        tail = f" (x{event.count})" if event.count > 1 else ""
        self._text.append(
            f'<span style="color:{colour}">[{event.severity.upper()}]</span> '
            f"<b>{event.source}/{event.title}</b>{tail}: {event.message}")
        self._text.moveCursor(QTextCursor.MoveOperation.End)


class ControllerLogWindow(QDialog):
    """Real-time display of gamepad/controller polling events."""
    def __init__(self, poller=None, parent=None):
        super().__init__(parent)
        self.poller = poller
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Controller Log Window")
        self.resize(500, 400)
        self._layout = QVBoxLayout(self)
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self._layout.addWidget(self.text_edit)

    def append_log(self, message):
        self.text_edit.append(message)
        
    def closeEvent(self, event):
        if self.poller:
            def print_log(msg):
                print(f"[controllerDrive] {msg}")
            self.poller.log_updater = print_log
        event.accept()


class QtDynamicView(QWidget):
    """
    A dynamic View widget that constructs its UI dynamically based on the 
    `ui_schema` provided by the model. 
    """
    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.poll_interval_ms = 50
        self._layout = QVBoxLayout(self)
        self.vars = {}  # attr -> QLineEdit/QLabel
        self.toggle_buttons = []
        # Controls whose availability depends on the model's mode
        # (enabled_when / disabled_when), plus composites refreshed on tick.
        self._gated = []
        self._log_streams = []
        self._plots = []
        self._regions = []
        self.log_window = None

        self._build_ui()
        
        # 1. UI Polling Timer (syncs UI fields from model)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_model)
        self.timer.start(self.poll_interval_ms)
        
        # The view keeps one render tick and nothing else (RC-4).
        #
        # Deleted from here: a 100 ms position timer, a 100 ms status timer,
        # a 20 ms manual-input timer, and the call that started the gamepad
        # poller on a QTimer-backed adapter. All four are loops the model
        # owns now, so the three frontends cannot drift apart on rate or on
        # neutral-on-exit — and the Web dashboard, which had none of them,
        # gets the same behaviour rather than none.

    def _build_ui(self):
        schema = getattr(self.model, 'ui_schema', {"sections": []})
        for section in schema.get("sections", []):
            card = QFrame()
            card.setObjectName("card")
            card_layout = QFormLayout(card)
            card_layout.setContentsMargins(10, 10, 10, 10)
            card_layout.setSpacing(10)
            
            title = section.get("title", "Section")
            lbl_title = QLabel(title)
            lbl_title.setProperty("class", "header")
            card_layout.addRow(lbl_title)
            
            elements = section.get("elements", [])
            for el in elements:
                row_layout = QHBoxLayout()
                el_type = el.get("type")
                label_text = el.get("text", "")
                
                if el_type in ["readonly", "entry"]:
                    attr = el.get("model_attr")
                    lbl = QLabel(label_text)
                    row_layout.addWidget(lbl)
                    
                    val = str(getattr(self.model, attr, ""))
                    if el_type == "readonly":
                        val_widget = QLabel(val)
                        val_widget.setObjectName("valueLabel")
                        val_widget.setToolTip(f"Current value of {label_text.replace(':', '')}")
                        self.vars[attr] = val_widget
                        row_layout.addWidget(val_widget)
                    else:
                        val_widget = QLineEdit(val)
                        val_widget.setToolTip(f"Edit {label_text.replace(':', '')}")
                        self.vars[attr] = val_widget
                        
                        # **Declared, not guessed.** This used to call
                        # `float(val)` on the field's current contents and
                        # treat a raised exception as "this is text", so a
                        # cleared box silently lost its validator for the rest
                        # of the session (RC-6 item 2).
                        is_numeric = el.get("value_type") in ("int", "float")

                        if is_numeric:
                            low = el.get("min")
                            high = el.get("max")
                            val_widget.setValidator(QDoubleValidator(
                                -1e9 if low is None else float(low),
                                1e9 if high is None else float(high),
                                int(el.get("decimals", 3)), val_widget))

                        def make_editor(attr_name, widget, num):
                            def commit():
                                text = widget.text()
                                if num:
                                    try:
                                        float(text)
                                        setattr(self.model, attr_name, text)
                                    except ValueError:
                                        widget.setText(str(getattr(self.model, attr_name, "")))
                                else:
                                    setattr(self.model, attr_name, text)
                            return commit

                        val_widget.editingFinished.connect(make_editor(attr, val_widget, is_numeric))
                        row_layout.addWidget(val_widget)
                        
                elif el_type == "button":
                    cmd_name = el.get("command")
                    btn = QPushButton(label_text)
                    btn.setToolTip(f"Execute {label_text.replace(':', '')}")
                    
                    btn.setProperty("role", el.get("role", "neutral"))

                    def make_cmd(element):
                        return lambda: self._run_element(element)

                    btn.clicked.connect(make_cmd(el))
                    row_layout.addWidget(btn)
                    self._gated.append({"widget": btn, "element": el})

                elif el_type == "toggle":
                    attr = el.get("model_attr")
                    cmd_name = el.get("command")
                    btn = QPushButton(el.get("false_text", "False"))
                    btn.setObjectName("toggleFalse")
                    btn.setToolTip(f"Toggle {label_text.replace(':', '')}")
                    
                    def make_cmd(element):
                        return lambda: self._run_element(element)

                    btn.clicked.connect(make_cmd(el))
                    self._gated.append({"widget": btn, "element": el})
                    self.toggle_buttons.append({
                        "widget": btn, "attr": attr,
                        "true_text": el.get("true_text", "True"), "false_text": el.get("false_text", "False")
                    })
                    row_layout.addWidget(btn)

                elif el_type == "dropdown":
                    attr = el.get("model_attr")
                    cmd_name = el.get("command")
                    options_cmd = el.get("options_command")
                    lbl = QLabel(label_text)
                    row_layout.addWidget(lbl)

                    options_func = getattr(self.model, options_cmd, None) if options_cmd else None
                    current_val = sch.current_text(self.model, el)
                    options = list(options_func()) if callable(options_func) else []
                    if current_val and current_val not in options:
                        options = [current_val] + options

                    combo = QComboBox()
                    combo.addItems(options)
                    if current_val in options:
                        combo.setCurrentText(current_val)
                    if attr:
                        self.vars[attr] = combo
                    row_layout.addWidget(combo)

                    def make_dropdown_cmd(c_name):
                        def handler(text):
                            if not text:
                                return
                            # Through `execute_command`, as Tk does. Calling
                            # the bound method directly skipped the D-5
                            # validate-then-run ordering in this renderer
                            # only, which is the divergence schema v2 exists
                            # to end.
                            # No local try/except: `execute_command` turns a
                            # raise into a `Failed` on the bus (RC-8 item 1).
                            # The modal that used to sit here is the second of
                            # the two that hung this suite.
                            self.model.execute_command(c_name, args=(text,))
                        return handler

                    combo.currentTextChanged.connect(make_dropdown_cmd(cmd_name))

                    def make_refresh(o_func, cb):
                        def handler():
                            cb.blockSignals(True)
                            cur = cb.currentText()
                            cb.clear()
                            opts = list(o_func()) if callable(o_func) else []
                            if cur and cur not in opts:
                                opts = [cur] + opts
                            cb.addItems(opts)
                            if cur in opts:
                                cb.setCurrentText(cur)
                            cb.blockSignals(False)
                        return handler

                    refresh_btn = QPushButton("⟳")
                    refresh_btn.setFixedWidth(28)
                    refresh_btn.setToolTip("Rescan available controllers")
                    refresh_btn.clicked.connect(make_refresh(options_func, combo))
                    row_layout.addWidget(refresh_btn)

                elif el_type == "file_save":
                    # Composite (S10 item 2): the view supplies the dialog,
                    # the model supplies the command. This replaces a
                    # `file_picker` branch whose first statement was
                    # `continue` — dead code that had rendered nothing since
                    # it was written (PYSIDE-11).
                    btn = QPushButton(label_text)
                    btn.setProperty("role", el.get("role", "neutral"))

                    def make_save(element):
                        def handler():
                            exts = element.get("extensions", ["csv"])
                            filt = ";;".join(
                                f"{e.upper()} files (*.{e})" for e in exts)
                            path, _ = QFileDialog.getSaveFileName(
                                self, element.get("text", "Save"), "", filt)
                            if path:
                                self._run_element(element, args=(path,))
                        return handler

                    btn.clicked.connect(make_save(el))
                    row_layout.addWidget(btn)

                elif el_type == "region_select":
                    btn = QPushButton(label_text)
                    btn.setProperty("role", el.get("role", "neutral"))

                    def make_region(element):
                        def handler():
                            # The overlay supplies the region; the *model*
                            # supplies the command (S10 item 2), which is what
                            # Tk already did. This arm still handed the
                            # overlay the model and let it assign
                            # `model.focus_area` itself, so the declared
                            # `set_focus_area` command never ran in PySide —
                            # one composite, two behaviours, which is the RC-7
                            # shape the composites exist to remove.
                            self.overlay = SelectionOverlay(
                                lambda x, y, w, h: self._run_element(
                                    element, args=(x, y, w, h)))
                            self.overlay.show()
                        return handler

                    btn.clicked.connect(make_region(el))
                    row_layout.addWidget(btn)

                    # known-issues #9: the drag used to give no on-screen
                    # confirmation of what it had captured, and the fix for
                    # that was the modal this arm just stopped raising. The
                    # composite already declares `model_attr`; showing it is
                    # the answer that does not block, and that the other two
                    # renderers can give as well.
                    if el.get("model_attr"):
                        region_label = QLabel(
                            sch.format_region(
                                getattr(self.model, el["model_attr"], None)))
                        row_layout.addWidget(region_label)
                        self._regions.append(
                            {"element": el, "widget": region_label})

                elif el_type == "plot":
                    # **D-6.** `plot` is "a live series the model supplies
                    # through `data_command`" — which Tk draws on a canvas and
                    # the Web client draws on a <canvas>, while this arm
                    # rendered a button that opened a dialog for loading a CSV
                    # off disk and never read `data_command` at all. One
                    # composite, two meanings: the RC-7 shape again. The
                    # series is drawn inline here now, like the other two.
                    #
                    # Reviewing a *past* run from a CSV is a different
                    # feature, it is PySide-only, and it is kept as its own
                    # button rather than being conflated with the live plot.
                    series_widget = SeriesPlot()
                    row_layout.addWidget(series_widget)
                    self._plots.append({"element": el, "widget": series_widget})

                    def make_plot(element):
                        def handler():
                            if getattr(self, "plot_dialog", None):
                                self.plot_dialog.deleteLater()
                            self.plot_dialog = PlotDialog(self, element=element)
                            self.plot_dialog.show()
                        return handler

                    csv_btn = QPushButton("Load CSV…")
                    csv_btn.setToolTip("Plot a saved run from a CSV file")
                    csv_btn.clicked.connect(make_plot(el))
                    row_layout.addWidget(csv_btn)

                elif el_type == "log_stream":
                    view = QTextEdit()
                    view.setReadOnly(True)
                    view.setMinimumHeight(140)
                    row_layout.addWidget(view)
                    self._log_streams.append({"element": el, "widget": view})

                elif el_type == "internal":
                    # Registers a command in the schema-derived allowlist
                    # without rendering anything. `row_layout` was never
                    # populated for this type, so it must not reach
                    # `addRow` below — an empty QHBoxLayout still adds a
                    # blank spaced row to the QFormLayout (PYSIDE-17).
                    continue

                card_layout.addRow(row_layout)
            self._layout.addWidget(card)
        self._layout.addStretch()

    #: `role` -> stylesheet. The schema names the meaning; the palette is
    #: this renderer's business. Elements used to carry raw bg/fg hex that
    #: only Tk could honour.
    ROLE_STYLES = {
        "neutral": "",
        "go": "background-color: #1b5e20; color: white;",
        "danger": "background-color: #7f0000; color: white;",
        "warning": "background-color: #e65100; color: black;",
        "info": "background-color: #0d47a1; color: white;",
    }

    def _gather_inputs(self, element):
        """The current *widget* text for each input the command declared (D-5).

        Reading the widgets rather than the model is the point: the model
        holds the last committed edit, which is one cycle behind what was just
        typed. Tk hid that with a `focus_set()` flush; copying it here was a
        named anti-fix, and the values travel with the command instead.
        """
        values = {}
        for name in element.get("inputs", []):
            widget = self.vars.get(name)
            if widget is not None and hasattr(widget, "text"):
                values[name] = widget.text()
            elif widget is not None and hasattr(widget, "currentText"):
                values[name] = widget.currentText()
            else:
                values[name] = getattr(self.model, name, "")
        return values

    def _run_element(self, element, args=None):
        """Run the element's command and render its `CommandResult`.

        **No `try/except QMessageBox` any more** (RC-8 item 1).
        `execute_command` catches, publishes to the bus and returns `Failed`,
        so the failure reaches the operator through the event log — and,
        because the bus marks it `requires_ack`, through exactly one modal
        raised by the subscriber rather than one raised here. The modal this
        method used to open is the one that hung the Qt suite for three
        sessions, and deleting it without a replacement path would have lost
        the report, so the report moved rather than went.
        """
        cmd_name = element.get("command")
        result = self.model.execute_command(
            cmd_name, inputs=self._gather_inputs(element), args=args)

        # The confirm contract (S10 item 3), one generic dialog per view.
        if result.needs_confirmation:
            reply = QMessageBox.question(
                self, "Confirm", result.prompt,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                result = self.model.execute_command(
                    result.command, args=(True,))

        return result

    def _execute_command(self, cmd_name):
        """Backwards-compatible entry point for a bare command name."""
        return self._run_element({"command": cmd_name})

    def _mode_name(self):
        mode = getattr(self.model, "mode", None)
        if mode is not None:
            return getattr(mode, "value", str(mode))
        if getattr(self.model, "monitoring", False):
            return "monitoring"
        return "idle"

    def _sync_gates(self):
        """One rule, `schema.is_enabled`, shared with the other two views."""
        mode = self._mode_name()
        for gate in self._gated:
            gate["widget"].setEnabled(sch.is_enabled(gate["element"], mode))

    def _redraw_plot(self, entry):
        """Hand the widget the model's current series. Same source as Tk."""
        source = getattr(self.model, entry["element"].get("data_command"), None)
        if not callable(source):
            return
        try:
            series = source() or {}
        except Exception:
            return
        entry["widget"].set_series(list(series.get("y", [])))

    def _refresh_log(self, entry):
        source = getattr(self.model, entry["element"].get("source_command"), None)
        if not callable(source):
            return
        try:
            lines = list(source() or [])
        except Exception:
            return
        text = "\n".join(lines[-40:])
        widget = entry["widget"]
        if widget.toPlainText() != text:
            widget.setPlainText(text)
            # `QTextCursor.MoveOperation.End`, not `cursor.End`: the latter is
            # not an instance attribute in PySide6, so this line raised
            # AttributeError on the first refresh that had anything to show —
            # out of `_poll_model`, which is a timer slot, so it also skipped
            # every widget after it in the same tick.
            widget.moveCursor(QTextCursor.MoveOperation.End)

    def _display(self, attr):
        """Render at the parameter's declared precision, not `str()`'s repr."""
        value = getattr(self.model, attr)
        param = getattr(self.model, "PARAMS", {}).get(attr)
        if param is not None and param.is_numeric:
            return param.format(value)
        return str(value)

    def _poll_model(self):
        # No hardware I/O on the render tick. This used to sample position and
        # status here *in addition to* the dedicated 100 ms timers above,
        # roughly tripling the serial traffic for one device. The render tick
        # reads the model's cached fields, which its own sampler fills.

        self._sync_gates()
        for entry in self._regions:
            text = sch.format_region(
                getattr(self.model, entry["element"]["model_attr"], None))
            if entry["widget"].text() != text:
                entry["widget"].setText(text)

        for entry in self._plots:
            self._redraw_plot(entry)

        for entry in self._log_streams:
            self._refresh_log(entry)

        for attr, widget in self.vars.items():
            if hasattr(self.model, attr):
                current_val = self._display(attr)
                if isinstance(widget, QLineEdit):
                    if not widget.hasFocus() and widget.text() != current_val:
                        widget.setText(current_val)
                elif isinstance(widget, QLabel):
                    if widget.text() != current_val:
                        widget.setText(current_val)
                elif isinstance(widget, QComboBox):
                    if not widget.hasFocus() and widget.currentText() != current_val:
                        if current_val in [widget.itemText(i) for i in range(widget.count())]:
                            widget.setCurrentText(current_val)
                        
        for tb in self.toggle_buttons:
            val = getattr(self.model, tb["attr"], False)
            widget = tb["widget"]
            
            current_state = widget.property("toggle_state")
            if current_state != val:
                if val:
                    widget.setText(tb["true_text"])
                    widget.setObjectName("toggleTrue")
                else:
                    widget.setText(tb["false_text"])
                    widget.setObjectName("toggleFalse")
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.setProperty("toggle_state", val)

    def cleanup(self):
        """Stop what this widget owns: its render tick and its own windows.

        **It used to close the model's gamepad poller**, and since D-1 made
        closing a dock a *hide*, that quietly ended the device's controller
        for the rest of the session — `ControllerPoller.close()` is terminal,
        `_closed` is never cleared and `start_polling` does not reset it, so
        re-showing the device brought back a model whose manual mode could
        never arm again. The model persisting through a hide is the whole
        content of D-1, and a poller is part of the model.

        Ending the device is `teardown()`'s job, reached through the manager
        (`release`, `shutdown_all`), which already stops and closes the poller
        in the right order relative to `power_down`. This was the second copy
        of that policy, in a place that had no business running it.

        The `pos_timer` / `status_timer` / `input_timer` / `disable_timer`
        branches went with it: those loops moved into the model in S5 and the
        attributes have not existed since, so the guards were dead code
        implying the view still had loops to stop.
        """
        if self.timer:
            self.timer.stop()
        if self.log_window:
            self.log_window.close()


class SeriesPlot(QWidget):
    """The `plot` composite's inline drawing surface.

    Deliberately a polyline over `QPainter` rather than an embedded
    matplotlib canvas: this is the live readout that ticks at the render
    rate, and Tk draws the same thing on a `tk.Canvas`. Reviewing a saved run
    is `PlotDialog`'s job, where matplotlib's axes and toolbar earn their
    cost.
    """

    _MARGIN_PX = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.setMinimumWidth(360)
        self._ys = []

    def set_series(self, ys):
        if ys != self._ys:
            self._ys = list(ys)
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111111"))
        if len(self._ys) < 2:
            return

        width = self.width()
        height = self.height()
        low, high = min(self._ys), max(self._ys)
        span = (high - low) or 1.0
        step = width / max(len(self._ys) - 1, 1)
        usable = height - 2 * self._MARGIN_PX

        pen = QPen(QColor("red"))
        pen.setWidth(2)
        painter.setPen(pen)
        previous = None
        for i, y in enumerate(self._ys):
            point = (i * step,
                     height - ((y - low) / span) * usable - self._MARGIN_PX)
            if previous is not None:
                painter.drawLine(int(previous[0]), int(previous[1]),
                                 int(point[0]), int(point[1]))
            previous = point


class SelectionOverlay(QWidget):
    """A full-screen drag to pick a rectangle. It reports; it does not write.

    It used to hold the model and assign `model.focus_area` on release, then
    pop an informational modal confirming what the operator had just drawn
    with their own mouse. Both are gone: the region goes to `on_region`,
    which the `region_select` composite wires to the model's declared
    command.
    """

    #: A drag smaller than this in either axis is a stray click, not a region.
    MIN_SIDE_PX = 10

    def __init__(self, on_region):
        super().__init__()
        self.on_region = on_region
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 100);")

        # Make fullscreen across all monitors
        screen_geom = QApplication.primaryScreen().geometry()
        for screen in QApplication.screens():
            screen_geom = screen_geom.united(screen.geometry())
        self.setGeometry(screen_geom)

        # PYSIDE-12: Add instruction label and instruction layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.instruction_label = QLabel("Click and drag to select focus area (ESC to cancel)")
        self.instruction_label.setStyleSheet("""
            QLabel {
                color: white;
                background-color: rgba(0, 0, 0, 200);
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.instruction_label, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self.setLayout(layout)

        self.start_pos_global = None
        self.end_pos_global = None
        self.start_pos_local = None
        self.end_pos_local = None

    def showEvent(self, event):
        """PYSIDE-12: Set focus, activate window, and show crosshair cursor on show."""
        super().showEvent(event)
        from PySide6.QtGui import QCursor
        # Request focus and activate the window
        self.setFocus()
        self.activateWindow()
        # Set crosshair cursor
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event):
        self.start_pos_global = event.globalPosition().toPoint()
        self.end_pos_global = self.start_pos_global
        self.start_pos_local = event.position().toPoint()
        self.end_pos_local = self.start_pos_local
        self.update()

    def mouseMoveEvent(self, event):
        self.end_pos_global = event.globalPosition().toPoint()
        self.end_pos_local = event.position().toPoint()
        self.update()

    def mouseReleaseEvent(self, event):
        if self.start_pos_global and self.end_pos_global:
            x1, x2 = sorted([self.start_pos_global.x(), self.end_pos_global.x()])
            y1, y2 = sorted([self.start_pos_global.y(), self.end_pos_global.y()])
            w = x2 - x1
            h = y2 - y1
            if w > self.MIN_SIDE_PX and h > self.MIN_SIDE_PX:
                self.on_region(int(x1), int(y1), int(w), int(h))
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def paintEvent(self, event):
        if hasattr(self, 'start_pos_local') and hasattr(self, 'end_pos_local') and self.start_pos_local and self.end_pos_local:
            painter = QPainter(self)
            pen = QPen(QColor("red"))
            pen.setWidth(3)
            painter.setPen(pen)
            
            x1, x2 = sorted([self.start_pos_local.x(), self.end_pos_local.x()])
            y1, y2 = sorted([self.start_pos_local.y(), self.end_pos_local.y()])
            painter.drawRect(x1, y1, x2 - x1, y2 - y1)


class PlotDialog(QDialog):
    def __init__(self, parent=None, element=None):
        self.element = element
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Data Plotter")
        self.resize(800, 600)
        self._layout = QVBoxLayout(self)

        self.top_frame = QHBoxLayout()
        self._layout.addLayout(self.top_frame)

        self.load_btn = QPushButton("Select & Load CSV File")
        self.load_btn.clicked.connect(self.load_csv)
        self.top_frame.addWidget(self.load_btn)

        self.plot_frame = QVBoxLayout()
        self._layout.addLayout(self.plot_frame)
        self.canvas = None
        self.toolbar = None
        self._csv_metadata = None

    def load_csv(self):
        from model.plot_data import parse_red_percent_csv, render_red_percent_figure
        filename, _ = QFileDialog.getOpenFileName(self, "Select Red Percent Log", "", "CSV Files (*.csv);;All Files (*)")
        if not filename: return
        try:
            # `newline=''` matches the Tk reference and `save_to_csv`'s own
            # writer: `csv` handles line endings itself, and opening without
            # it lets a CRLF file be double-translated on Windows.
            with open(filename, 'r', newline='') as f:
                parsed = parse_red_percent_csv(f.read())
        except Exception as e:
            # ERRORS-9: Route through ErrorRouter instead of direct QMessageBox
            ErrorRouter.report_error("Failed to Load CSV", f"Failed to load CSV: {e}", e)
            return
        # A header-only CSV (or one with a dims column but zero data rows)
        # has an empty `dims` *and* an empty `red_percents`, or a non-empty
        # `dims` with still-empty `red_percents` — either way there is
        # nothing to plot. Gating on `dims` alone let that case slip through
        # and draw an empty plot instead of reporting the real problem.
        if not parsed["red_percents"]:
            # ERRORS-9: Route through ErrorRouter instead of direct QMessageBox
            ErrorRouter.report_error("Invalid CSV File", "CSV missing 'Red Percent' column")
            return
        self._csv_metadata = parsed["metadata"]
        self.select_plot_type(parsed["dims"], parsed["red_percents"], parsed["dim_data"])

    def select_plot_type(self, dims_found, red_percents, dim_data):
        if not dims_found:
            self.draw_plot("0D", None, None, None, red_percents, dim_data)
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Plot Type")
        dlg_layout = QVBoxLayout(dialog)
        
        dlg_layout.addWidget(QLabel("Select the type of plot:"))
        
        plot_type_combo = QComboBox()
        plot_type_combo.addItems(["0D (Time/Index)", "1D (Single Dimension)"])
        if len(dims_found) >= 2: plot_type_combo.addItem("2D (Two Dimensions)")
        if len(dims_found) >= 3: plot_type_combo.addItem("3D (Three Dimensions)")
        plot_type_combo.setCurrentIndex(plot_type_combo.count() - 1)
        dlg_layout.addWidget(plot_type_combo)
        
        form = QFormLayout()
        dim1_cb = QComboBox(); dim1_cb.addItems(dims_found)
        dim2_cb = QComboBox(); dim2_cb.addItems(dims_found)
        dim3_cb = QComboBox(); dim3_cb.addItems(dims_found)
        
        form.addRow("Dim 1:", dim1_cb)
        form.addRow("Dim 2:", dim2_cb)
        form.addRow("Dim 3:", dim3_cb)
        dlg_layout.addLayout(form)
        
        btn = QPushButton("Plot Data")
        dlg_layout.addWidget(btn)
        
        selected = {}
        def on_ok():
            pt = plot_type_combo.currentText().split()[0]
            selected['type'] = pt
            selected['dim1'] = dim1_cb.currentText()
            selected['dim2'] = dim2_cb.currentText()
            selected['dim3'] = dim3_cb.currentText()
            dialog.accept()
            
        btn.clicked.connect(on_ok)
        dialog.exec()
        
        if 'type' in selected:
            self.draw_plot(selected['type'], selected['dim1'], selected['dim2'], selected['dim3'], red_percents, dim_data)

    def draw_plot(self, plot_type, dim1, dim2, dim3, red_percents, dim_data):
        from model.plot_data import render_red_percent_figure
        if self.canvas:
            self.plot_frame.removeWidget(self.canvas)
            self.canvas.deleteLater()
        if self.toolbar:
            self.plot_frame.removeWidget(self.toolbar)
            self.toolbar.deleteLater()

        fig = render_red_percent_figure(plot_type, dim1, dim2, dim3, red_percents, dim_data)

        # Tk sets the plot title from the CSV's own metadata block (probe
        # name and tilt); this renderer ignored `parsed["metadata"]`
        # entirely. `render_red_percent_figure` is shared with the Tk and
        # Web views and already sets a generic title per axis, so the probe
        # identity is appended here rather than duplicating that logic.
        meta = self._csv_metadata or {}
        probe = meta.get("Probe Name")
        tilt = meta.get("Probe Tilt Angle")
        if probe or tilt:
            parts = []
            if probe:
                parts.append(f"Probe: {probe}")
            if tilt:
                parts.append(f"Tilt: {tilt}")
            suffix = " (" + ", ".join(parts) + ")"
            for ax in fig.axes:
                ax.set_title(ax.get_title() + suffix)

        self.canvas = FigureCanvasQTAgg(fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        
        self.plot_frame.addWidget(self.toolbar)
        self.plot_frame.addWidget(self.canvas)


class RedPercentDynamicView(QtDynamicView):
    """Red Percent, rendered from the schema like every other device (D-6).

    **What used to be here is gone.** A hand-built "Position Source" combo
    duplicated the schema's dropdown, and a hand-built "Save Log" button
    duplicated what is now a `file_save` composite — each reaching into the
    model directly, each drifting from the schema, and neither reachable from
    the Web client. The schema publishes both.

    What remains is the one thing the schema cannot express: **D-10's prompt
    on stopping a run with unsaved data.** That is a view-side question about
    a modal the operator must answer, not a control.
    """

    def _run_element(self, element, args=None):
        """Only D-10's unsaved-data prompt is special here.

        `set_focus_area_ui`, `plot_data_ui` and `save_log_web` used to be
        intercepted by name in this method — three view-side shims standing in
        for element types the schema had no way to express. They are
        `region_select`, `plot` and `file_save` composites now, handled by the
        base renderer, so this override is down to one genuinely view-side
        question.
        """
        super()._run_element(element, args=args)
        if element.get("command") == "stop_monitoring" and self.model.has_unsaved_data:
            reply = QMessageBox.question(
                self, "Save Log",
                "Monitoring stopped. Would you like to save the data to a CSV?",
                QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.save_log_ui()

    def save_log_ui(self):
        """REDPERCENT-6: Route through model.save_log, not data_log.save_to_csv.

        The model method syncs probe_name and probe_tilt_angle from the model
        to the data_log before saving, and also writes the station_meta.json
        sidecar with the full run configuration.
        """
        if not self.model.data_log or not self.model.data_log.red_values:
            # ERRORS-9: Route through ErrorRouter instead of direct QMessageBox
            ErrorRouter.report_warning("No Data", "No data to save.")
            return

        p_name = getattr(self.model, 'probe_name', None)
        if not p_name or not str(p_name).strip():
            p_name = "red_log"
        default_name = f"{p_name}.csv".replace(' ', '_')

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Red Detection Log", default_name, "CSV Files (*.csv);;All Files (*)"
        )
        if file_path:
            # Tk's file_save composite passes `defaultextension=".csv"` to
            # `asksaveasfilename`, which Tk enforces itself. Qt's
            # `getSaveFileName` has no equivalent: the filter string is a
            # display hint only, so a bare filename (most visibly on Linux,
            # where the native dialog does not append the filter's
            # extension) saves with none at all.
            if not os.path.splitext(file_path)[1]:
                file_path += ".csv"
            # Call model.save_log which syncs metadata and saves both CSV and sidecar
            self.model.save_log(file_path)

    def cleanup(self):
        """PYSIDE-4: D-10's discard prompt runs here via the model's teardown.

        The confirm_discard hook is set before teardown so that when the model
        checks whether to autosave unsaved data, it can ask the operator first.
        """
        super().cleanup()

        # D-10 seam: install the discard prompt hook before teardown
        if hasattr(self.model, 'has_unsaved_data') and self.model.has_unsaved_data:
            def on_confirm_discard():
                """Ask the operator whether to save or discard unsaved data.

                Returns True if operator chose to discard (so autosave is skipped),
                False/None if they chose to save (so autosave happens).
                """
                reply = QMessageBox.question(
                    self, "Unsaved Data",
                    "Monitoring has unsaved data. Save before closing?",
                    QMessageBox.Yes | QMessageBox.No)
                # Return True if user chose No (Discard), False if Yes (Save)
                return reply == QMessageBox.No

            self.model.confirm_discard = on_confirm_discard

        # **Not** teardown. `cleanup()` runs on the D-1 *hide* path
        # (`close_device_view`, whose own comment two lines below its call
        # site reads "D-1: closing means *hide*. The view used to destroy the
        # model"). `teardown()` unbinds the registry and ends the run, so
        # calling it here would reintroduce precisely the RC-1 defect S2 and
        # S6 removed — a closed dock that destroys its device.
        #
        # Installing the hook above is enough. The real teardown happens in
        # `MainWindow.closeEvent` via `system_manager.shutdown_all()`, and it
        # consults the hook we just set, so D-10's autosave still runs at
        # shutdown. On a hide, the model persists and so does its data, which
        # is why nothing needs saving here for the data to survive.
        if hasattr(self.model, 'stop_monitoring'):
            self.model.stop_monitoring()

        if hasattr(self, 'plot_dialog') and self.plot_dialog:
            self.plot_dialog.close()


class DeviceDock(QDockWidget):
    closed = Signal()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # WA_DeleteOnClose stays: a programmatic close still destroys the
        # *widget*, which is correct and necessary — without it, unchecking and
        # re-checking a device in the sidebar builds a fresh dock each time and
        # leaves the old one alive as a hidden child. What the model does is no
        # longer the dock's business either way; S2 removed the model teardown
        # from this path entirely.
        self.setAttribute(Qt.WA_DeleteOnClose)
        # Closable again (S6). The affordance was withdrawn in S2 because
        # closing would have destroyed the widget that owned the device's
        # control loops. S5 moved those into the model, so a closed dock is
        # now exactly what D-1 says it is — a hidden device that is still
        # running, reachable from the sidebar, and de-energized by the
        # manager on the way out (D-2).
        self.setFeatures(QDockWidget.DockWidgetMovable
                         | QDockWidget.DockWidgetFloatable
                         | QDockWidget.DockWidgetClosable)

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)

class DashboardWindow(QMainWindow):
    def __init__(self, system_manager):
        super().__init__()
        self.system_manager = system_manager
        self.setWindowTitle("Unified Control Dashboard (PySide6)")
        self.resize(1200, 800)
        self.setDockOptions(QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks)
        
        style_path = os.path.join(os.path.dirname(__file__), "style.qss")
        try:
            with open(style_path, "r") as f:
                self.setStyleSheet(f.read())
        except Exception as e:
            print(f"Failed to load stylesheet: {e}")
        
        # Sidebar
        self.sidebar = QDockWidget("Device Manager", self)
        self.sidebar.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.sidebar.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.sidebar.setMinimumWidth(250)
        
        sidebar_widget = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)
        
        self.device_list = QListWidget()
        self.device_list.setStyleSheet("background-color: #1E1E1E; color: white; border: none;")
        sidebar_layout.addWidget(self.device_list)

        # Bottom-docked, not top: matches the Tk reference, whose own comment
        # explains why — sitting directly above the tab bar (here: directly
        # above the device checkboxes that hide/show and destroy models) made
        # it an easy accidental-click target when reaching for something
        # else. `fullStopButton` gets its own QSS rule so hover/pressed states
        # read as a distinct, deliberate control rather than a checkbox.
        self.stop_btn = QPushButton("FULL STOP")
        self.stop_btn.setObjectName("fullStopButton")
        self.stop_btn.clicked.connect(self.system_manager.full_stop_all)
        sidebar_layout.addWidget(self.stop_btn)

        self.sidebar.setWidget(sidebar_widget)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.sidebar)

        self.device_list.itemChanged.connect(self.on_device_item_changed)

        # No central widget: one was set here only to give QMainWindow
        # "an empty workspace", but QMainWindow does not require one, and an
        # empty QWidget still claims a stretch share of the layout, squeezing
        # every dock (PYSIDE-16). Leaving it unset lets the docks use the
        # whole window, which is what this window is for.

        self.active_docks = {}
        self._last_added_dock = None
        self.populate_sidebar()

    def changeEvent(self, event):
        if event.type() in (QEvent.WindowActivate, QEvent.WindowDeactivate):
            # Deferred by one event-loop turn on purpose. On deactivate, Qt has
            # not yet made the *new* window active, so asking right now cannot
            # distinguish "the operator switched to another application" from
            # "this application opened a modal dialog" — and the second must
            # not gate input (D-4).
            QTimer.singleShot(0, self._sync_input_gate)
        super().changeEvent(event)

    def _app_has_focus(self):
        """True while any window of *this* application is active.

        A child dialog — a file picker, a rotation confirmation, an error box
        — keeps the application focused even though the main window is
        deactivated. Treating that as focus loss is the defect behind
        PYSIDE-14.
        """
        app = QApplication.instance()
        return bool(app and app.activeWindow() is not None)

    def _sync_input_gate(self):
        """D-4: gate controller input while unfocused. Never stop."""
        is_open = self._app_has_focus()
        for model in self.system_manager.get_active_models_snapshot().values():
            setter = getattr(model, "set_input_gate", None)
            if callable(setter):
                setter(is_open)

    def populate_sidebar(self):
        self.device_list.blockSignals(True)
        self.device_list.clear()
        
        # The one registry (RC-7). This was a fourth copy of the device
        # list, which is why the sidebar could offer a device the builder had
        # never heard of.
        all_devices = devices.names()

        for name in all_devices:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            
            # Auto-open only if it was selected at startup (already active)
            if self.system_manager.get_model(name) is not None:
                item.setCheckState(Qt.Checked)
                self.open_device_view(name)
            else:
                item.setCheckState(Qt.Unchecked)
            self.device_list.addItem(item)
            
        self.device_list.blockSignals(False)

    def on_device_item_changed(self, item):
        device_name = item.text()
        if item.checkState() == Qt.Checked:
            self.open_device_view(device_name)
        else:
            self.close_device_view(device_name)
            
    def close_device_view(self, device_name):
        if device_name in self.active_docks:
            dock = self.active_docks.pop(device_name)
            if self._last_added_dock == dock:
                # Fall back to whichever dock is now the most recently
                # added survivor (dict insertion order), not None — None
                # made the *next* opened dock take the addDockWidget(Right)
                # branch in open_device_view instead of continuing the
                # splitDockWidget horizontal chain, so closing the
                # rightmost dock silently changed where the next one
                # landed (PYSIDE-16).
                remaining = list(self.active_docks.values())
                self._last_added_dock = remaining[-1] if remaining else None

            # Stop the view's QTimers before the dock's WA_DeleteOnClose
            # schedules its widget for deletion -- otherwise a timer tick
            # (e.g. RedPercentDynamicView's 50ms _poll_model, refreshing
            # live-changing readonly fields) can land after Qt has already
            # destroyed the underlying C++ widget, raising "Internal C++
            # object already deleted".
            widget = dock.widget()
            if hasattr(widget, 'cleanup'):
                try:
                    widget.cleanup()
                except Exception:
                    pass

            dock.close()

            # D-1: closing means *hide*. The view used to destroy the model
            # here with its own hasattr ladder — a third copy of the teardown
            # policy, in the wrong order, ending in a raw `del` from the
            # manager's dict. Lifetime is the manager's (I-1.5) and visibility
            # is all the view gets to decide, so it says so and stops there.
            # The manager brings the hardware to a safe state per D-2.
            self.system_manager.hide(device_name)

    def _confirm_rotation_dialog(self, target_deg: float) -> bool:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Rotation Limit Warning")
        msg.setText(f"Target rotation {target_deg:.2f}° exceeds the safe ±30° range.\n\nMoving past this limit risks damaging physical tubing.\n\nAre you sure you want to proceed?")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        return msg.exec() == QMessageBox.Yes

    def open_device_view(self, device_name):
        # `show` is what un-hides the device; it never constructs one. A dock
        # that is still built is reused rather than rebuilt, so reopening a
        # device shows the *existing* model with its port and controller
        # binding intact — which is the whole point of D-1.
        model = self.system_manager.show(device_name)

        if device_name in self.active_docks:
            dock = self.active_docks[device_name]
            dock.show()
            dock.raise_()
            dock.activateWindow()
            return

        if not model:
            # The view no longer constructs models (RC-1 item 5). It used to
            # fabricate StepperProbe(None, "None", {}) and friends here, which
            # produced a *silent headless model*: every control rendered and
            # responded, but nothing was attached to any hardware, and the
            # operator had no way to tell (PYSIDE-1, MANAGER-8, ROTATOR-5,
            # TEMP-6). A device that was not configured at setup is simply not
            # available; say so and leave the checkbox unticked.
            QMessageBox.information(
                self, "Device Not Configured",
                f"{device_name} was not configured at startup.\n\n"
                "Restart and select it in the setup window to use it.")
            self._set_sidebar_checked(device_name, False)
            return
            
        # The rotation-confirmation callback the view used to inject here is
        # gone (S10 item 3): the model returns NeedsConfirmation and every
        # renderer asks it with one generic dialog. Injecting it meant the
        # check only existed in whichever frontend remembered to inject —
        # never the Web client.
        dock = DeviceDock(device_name, self)
        dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        
        # Routing by a `custom_view_class` the *model* declares, rather than
        # by a device-name literal here. One fewer copy of the device list
        # (RC-7, I-7.1).
        view_class = VIEW_CLASSES.get(
            getattr(model, "VIEW_HINT", None), QtDynamicView)
        view_widget = view_class(model)
        dock.setWidget(view_widget)
        
        dock.closed.connect(lambda: self.on_dock_closed(device_name))
        
        if self._last_added_dock:
            self.splitDockWidget(self._last_added_dock, dock, Qt.Horizontal)
        else:
            self.addDockWidget(Qt.RightDockWidgetArea, dock)
            
        self._last_added_dock = dock
        self.active_docks[device_name] = dock
        
    def _set_sidebar_checked(self, device_name, checked):
        """Set a sidebar checkbox without re-entering on_device_item_changed."""
        self.device_list.blockSignals(True)
        try:
            for i in range(self.device_list.count()):
                item = self.device_list.item(i)
                if item.text() == device_name:
                    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
                    break
        finally:
            self.device_list.blockSignals(False)

    def on_dock_closed(self, device_name):
        self._set_sidebar_checked(device_name, False)
        if device_name in self.active_docks:
            self.close_device_view(device_name)

    def closeEvent(self, event):
        # Stop all dynamic view timers and pollers
        for name, dock in self.active_docks.items():
            widget = dock.widget()
            if hasattr(widget, 'cleanup'):
                try:
                    widget.cleanup()
                except Exception:
                    pass
        self.system_manager.shutdown_all()
        event.accept()


#: Hint -> Qt widget. See the Tk module's twin: a device earns an entry only
#: for behaviour the schema cannot express.
VIEW_CLASSES = {
    "red_percent": RedPercentDynamicView,
}
