import sys
import os
import csv
import traceback
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QDockWidget, QListWidget, QWidget, 
    QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QFrame, 
    QMessageBox, QListWidgetItem, QDialog, QFileDialog, QFormLayout, 
    QComboBox, QTextEdit, QCheckBox
)
from PySide6.QtCore import Qt, QTimer, QObject, Signal
from PySide6.QtGui import QPainter, QColor, QPen

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure

from error_routing import ErrorRouter


class QtErrorPopupManager(QObject):
    """
    Centralized error handler for PySide6 that safely routes error popups
    from any background thread to the main Qt GUI thread via Qt Signals.
    """
    _instance = None
    _message_signal = Signal(str, str, str, object)  # type, title, message, exception

    def __init__(self, parent=None):
        super().__init__(parent)
        self._message_signal.connect(self._display_popup)
        ErrorRouter.set_callbacks(self.report_error, self.report_warning, self.report_info)

    @classmethod
    def initialize(cls, parent=None):
        if cls._instance is None:
            cls._instance = QtErrorPopupManager(parent)
        return cls._instance

    @classmethod
    def report_error(cls, title, message, exception=None):
        if cls._instance:
            cls._instance._message_signal.emit('error', title, message, exception)
        else:
            print(f"[ERROR] {title}: {message}")
            if exception:
                traceback.print_exc()

    @classmethod
    def report_warning(cls, title, message, exception=None):
        if cls._instance:
            cls._instance._message_signal.emit('warning', title, message, exception)
        else:
            print(f"[WARNING] {title}: {message}")

    @classmethod
    def report_info(cls, title, message):
        if cls._instance:
            cls._instance._message_signal.emit('info', title, message, None)
        else:
            print(f"[INFO] {title}: {message}")

    def _display_popup(self, msg_type, title, message, exception):
        full_message = message
        if exception:
            if not full_message:
                full_message = ""
            try:
                full_message += f"\n\nDetails:\n{type(exception).__name__}: {str(exception)}"
            except Exception:
                full_message += "\n\nDetails: <Unprintable Exception>"
        if full_message and len(full_message) > 5000:
            full_message = full_message[:5000] + "... [TRUNCATED]"

        if msg_type == 'error':
            QMessageBox.critical(None, title, full_message)
        elif msg_type == 'warning':
            QMessageBox.warning(None, title, full_message)
        else:
            QMessageBox.information(None, title, full_message)

    @classmethod
    def setup_excepthook(cls):
        """Hook into sys.excepthook to catch all unhandled exceptions globally."""
        def custom_excepthook(exc_type, exc_value, exc_traceback):
            try:
                traceback.print_exception(exc_type, exc_value, exc_traceback)
            except Exception:
                print(f"Exception: {exc_value}")
            cls.report_error(
                "Unhandled Exception",
                f"An unexpected error occurred:\n\n{exc_value}",
                exception=exc_value
            )
        sys.excepthook = custom_excepthook


class ControllerLogWindow(QDialog):
    """Real-time display of gamepad/controller polling events."""
    def __init__(self, poller=None, parent=None):
        super().__init__(parent)
        self.poller = poller
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Controller Log Window")
        self.resize(500, 400)
        self.layout = QVBoxLayout(self)
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.layout.addWidget(self.text_edit)
        self.setStyleSheet(
            "QWidget { background-color: #121212; color: #FFFFFF; } "
            "QTextEdit { background-color: #1E1E1E; border: 1px solid #333; padding: 5px; color: lightgreen; font-family: 'Courier New', monospace; }"
        )

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
        self.layout = QVBoxLayout(self)
        self.vars = {}  # attr -> QLineEdit/QLabel
        self.toggle_buttons = []
        self.log_window = None
        
        self.setStyleSheet("""
            QWidget { background-color: #121212; color: #FFFFFF; font-family: 'Segoe UI', sans-serif; }
            QLabel.header { font-weight: bold; color: #0078D4; margin-top: 10px; }
            QLineEdit { background-color: #1E1E1E; border: 1px solid #333; padding: 5px; color: #FFF; }
            QPushButton { background-color: #0078D4; color: white; font-weight: bold; padding: 5px; border-radius: 3px; }
            QPushButton:hover { background-color: #107C10; }
            QFrame { background-color: #1E1E1E; border-radius: 5px; }
        """)
        
        self._build_ui()
        
        # 1. UI Polling Timer (syncs UI fields from model)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_model)
        self.timer.start(self.poll_interval_ms)
        
        # 2. Hardware Live Polling Timers (read_position & poll_status)
        if hasattr(self.model, 'read_position'):
            self.pos_timer = QTimer(self)
            self.pos_timer.timeout.connect(self._safe_read_position)
            self.pos_timer.start(100)
            
        if hasattr(self.model, 'poll_status'):
            self.status_timer = QTimer(self)
            self.status_timer.timeout.connect(self._safe_poll_status)
            self.status_timer.start(100)
        
        # 3. Gamepad Poller and Manual Mode Loop
        if hasattr(self.model, 'poller') and self.model.poller:
            class GUIAdapter:
                def after(self, ms, func):
                    QTimer.singleShot(ms, func)
            
            def _reset_disable_timer(model_ref=self.model):
                if hasattr(self, 'disable_timer') and self.disable_timer:
                    self.disable_timer.stop()
                def _do_disable():
                    msg = f"5 minutes of inactivity detected. Disabling {model_ref.__class__.__name__}"
                    print(f"[Timeout] {msg}")
                    QtErrorPopupManager.report_info("Idle Timeout", msg)
                    if hasattr(model_ref, 'disable'):
                        model_ref.disable()
                if getattr(model_ref, 'system_enabled', False):
                    if not hasattr(self, 'disable_timer') or self.disable_timer is None:
                        self.disable_timer = QTimer(self)
                        self.disable_timer.setSingleShot(True)
                        self.disable_timer.timeout.connect(_do_disable)
                    self.disable_timer.start(300000)

            def print_log(msg):
                print(f"[controllerDrive] {msg}")

            self.model.poller.start_polling(GUIAdapter(), log_updater=print_log, activity_callback=_reset_disable_timer)
            
            self.input_timer = QTimer(self)
            def _route_input():
                if getattr(self.model, 'manual_flag', False):
                    controller_params = self.model.poller.get_mapped_state()
                    if hasattr(self.model, 'send_manual_mode_command'):
                        self.model.send_manual_mode_command(controller_params)
            self.input_timer.timeout.connect(_route_input)
            self.input_timer.start(20)

    def _safe_read_position(self):
        try:
            self.model.read_position()
        except Exception:
            pass

    def _safe_poll_status(self):
        try:
            self.model.poll_status()
        except Exception:
            pass

    def _build_ui(self):
        schema = getattr(self.model, 'ui_schema', {"sections": []})
        for section in schema.get("sections", []):
            card = QFrame()
            card_layout = QVBoxLayout(card)
            
            title = section.get("title", "Section")
            lbl_title = QLabel(title)
            lbl_title.setProperty("class", "header")
            card_layout.addWidget(lbl_title)
            
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
                        val_widget.setStyleSheet("color: lightgreen; font-weight: bold;")
                        self.vars[attr] = val_widget
                        row_layout.addWidget(val_widget)
                    else:
                        val_widget = QLineEdit(val)
                        self.vars[attr] = val_widget
                        
                        def make_editor(attr_name, widget):
                            return lambda text: setattr(self.model, attr_name, text)
                            
                        val_widget.textChanged.connect(make_editor(attr, val_widget))
                        row_layout.addWidget(val_widget)
                        
                elif el_type == "button":
                    cmd_name = el.get("command")
                    btn = QPushButton(label_text)
                    
                    def make_cmd(c_name):
                        return lambda: self._execute_command(c_name)
                        
                    btn.clicked.connect(make_cmd(cmd_name))
                    row_layout.addWidget(btn)
                    
                elif el_type == "toggle":
                    attr = el.get("model_attr")
                    cmd_name = el.get("command")
                    btn = QPushButton(el.get("false_text", "False"))
                    
                    def make_cmd(c_name):
                        return lambda: self._execute_command(c_name)
                        
                    btn.clicked.connect(make_cmd(cmd_name))
                    self.toggle_buttons.append({
                        "widget": btn, "attr": attr, 
                        "true_text": el.get("true_text"), "false_text": el.get("false_text")
                    })
                    row_layout.addWidget(btn)

                elif el_type == "dropdown":
                    attr = el.get("model_attr")
                    cmd_name = el.get("command")
                    options_cmd = el.get("options_command")
                    lbl = QLabel(label_text)
                    row_layout.addWidget(lbl)

                    options_func = getattr(self.model, options_cmd, None) if options_cmd else None
                    current_val = str(getattr(self.model, attr, ""))
                    options = list(options_func()) if callable(options_func) else []
                    if current_val and current_val not in options:
                        options = [current_val] + options

                    combo = QComboBox()
                    combo.addItems(options)
                    if current_val in options:
                        combo.setCurrentText(current_val)
                    row_layout.addWidget(combo)

                    def make_dropdown_cmd(c_name):
                        def handler(text):
                            if not text:
                                return
                            func = getattr(self.model, c_name, None)
                            if func and callable(func):
                                try:
                                    func(text)
                                except Exception as e:
                                    QMessageBox.critical(self, "Command Failed", f"Command {c_name} failed:\n{e}")
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

                elif el_type == "file_picker":
                    cmd_name = el.get("command")
                    btn = QPushButton(label_text)
                    btn.setStyleSheet("background-color: darkorange; color: black; font-weight: bold;")
                    file_lbl = QLabel("No Script Selected")
                    file_lbl.setStyleSheet("color: yellow; font-size: 11px;")
                    
                    def make_file_cmd(c_name, lbl_widget):
                        def wrapped():
                            path, _ = QFileDialog.getOpenFileName(
                                self, 
                                "Select Script File", 
                                "", 
                                "Text and GCode files (*.txt *.gcode *.nc);;All Files (*)"
                            )
                            if path:
                                lbl_widget.setText(os.path.basename(path))
                                func = getattr(self.model, c_name, None)
                                if func and callable(func):
                                    func(path)
                        return wrapped
                        
                    btn.clicked.connect(make_file_cmd(cmd_name, file_lbl))
                    row_layout.addWidget(btn)
                    row_layout.addWidget(file_lbl)
                    
                card_layout.addLayout(row_layout)
            self.layout.addWidget(card)
        self.layout.addStretch()

    def _execute_command(self, cmd_name):
        if cmd_name == "open_controller_log":
            poller = getattr(self.model, 'poller', None)
            if not hasattr(self, 'log_window') or self.log_window is None:
                self.log_window = ControllerLogWindow(poller=poller, parent=self)
                self.log_window.destroyed.connect(lambda: setattr(self, 'log_window', None))
                if poller:
                    poller.log_updater = self.log_window.append_log
                self.log_window.show()
            else:
                if poller:
                    poller.log_updater = self.log_window.append_log
                self.log_window.show()
                self.log_window.raise_()
                self.log_window.activateWindow()
            return
            
        func = getattr(self.model, cmd_name, None)
        if func and callable(func):
            try:
                func()
            except Exception as e:
                QMessageBox.critical(self, "Command Failed", f"Command {cmd_name} failed:\n{e}")

    def _poll_model(self):
        if hasattr(self.model, 'read_position'):
            self.model.read_position()
        if hasattr(self.model, 'poll_status'):
            self.model.poll_status()

        for attr, widget in self.vars.items():
            if hasattr(self.model, attr):
                current_val = str(getattr(self.model, attr))
                if isinstance(widget, QLineEdit):
                    if not widget.hasFocus() and widget.text() != current_val:
                        widget.setText(current_val)
                elif isinstance(widget, QLabel):
                    if widget.text() != current_val:
                        widget.setText(current_val)
                        
        for tb in self.toggle_buttons:
            val = getattr(self.model, tb["attr"], False)
            widget = tb["widget"]
            
            current_state = widget.property("toggle_state")
            if current_state != val:
                if val:
                    widget.setText(tb["true_text"])
                    widget.setStyleSheet("background-color: #107C10; color: white; font-weight: bold;")
                else:
                    widget.setText(tb["false_text"])
                    widget.setStyleSheet("background-color: #D13438; color: white; font-weight: bold;")
                widget.setProperty("toggle_state", val)

    def cleanup(self):
        """Stop all timers and release poller/hardware references."""
        if hasattr(self, 'timer') and self.timer:
            self.timer.stop()
        if hasattr(self, 'pos_timer') and self.pos_timer:
            self.pos_timer.stop()
        if hasattr(self, 'status_timer') and self.status_timer:
            self.status_timer.stop()
        if hasattr(self, 'input_timer') and self.input_timer:
            self.input_timer.stop()
        if hasattr(self, 'disable_timer') and self.disable_timer:
            self.disable_timer.stop()
        if hasattr(self, 'log_window') and self.log_window:
            self.log_window.close()
        if hasattr(self.model, 'poller') and self.model.poller:
            self.model.poller.stop_polling()
            self.model.poller.close()


class SelectionOverlay(QWidget):
    def __init__(self, model):
        super().__init__()
        self.model = model
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background-color: rgba(0, 0, 0, 100);")
        
        # Make fullscreen across all monitors
        screen_geom = QApplication.primaryScreen().geometry()
        for screen in QApplication.screens():
            screen_geom = screen_geom.united(screen.geometry())
        self.setGeometry(screen_geom)
        
        self.start_pos_global = None
        self.end_pos_global = None
        self.start_pos_local = None
        self.end_pos_local = None

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
            if w > 10 and h > 10:
                self.model.focus_area = {'top': int(y1), 'left': int(x1), 'width': int(w), 'height': int(h)}
                print(f"Captured Focus Area: {self.model.focus_area}")
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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Data Plotter")
        self.resize(800, 600)
        self.layout = QVBoxLayout(self)

        self.top_frame = QHBoxLayout()
        self.layout.addLayout(self.top_frame)

        self.load_btn = QPushButton("Select & Load CSV File")
        self.load_btn.clicked.connect(self.load_csv)
        self.top_frame.addWidget(self.load_btn)

        self.plot_frame = QVBoxLayout()
        self.layout.addLayout(self.plot_frame)
        self.canvas = None
        self.toolbar = None

    def load_csv(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Select Red Percent Log", "", "CSV Files (*.csv);;All Files (*)")
        if not filename: return
        
        try:
            with open(filename, 'r') as f:
                reader = csv.reader(f)
                header = next(reader)
                
                try: red_idx = header.index("Red Percent")
                except ValueError:
                    QMessageBox.critical(self, "Invalid File", "CSV missing 'Red Percent' column")
                    return
                    
                dim_indices = {}
                for i, col in enumerate(header):
                    if col not in ["Timestamp", "Red Percent"] and col.strip():
                        dim_indices[col] = i
                        
                red_percents = []
                dim_data = {dim: [] for dim in dim_indices.keys()}
                
                for row in reader:
                    if not row: continue
                    try:
                        r_val = float(row[red_idx])
                        red_percents.append(r_val)
                        for dim, idx in dim_indices.items():
                            if len(row) > idx:
                                dim_data[dim].append(float(row[idx]))
                    except ValueError:
                        continue
                        
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load CSV: {e}")
            return
            
        dims_found = list(dim_data.keys())
        self.select_plot_type(dims_found, red_percents, dim_data)

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
        plot_type_combo.setCurrentIndex(len(plot_type_combo) - 1)
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
        if self.canvas:
            self.plot_frame.removeWidget(self.canvas)
            self.canvas.deleteLater()
        if self.toolbar:
            self.plot_frame.removeWidget(self.toolbar)
            self.toolbar.deleteLater()

        fig = Figure(figsize=(8, 6), dpi=100)
        
        if plot_type == "0D":
            ax = fig.add_subplot(111)
            ax.plot(red_percents, marker='o', linestyle='-', color='b')
            ax.set_xlabel('Index (Time / Samples)')
            ax.set_ylabel('Red Percent')
            ax.set_title('Red Percent Data')
            ax.grid(True)
        elif plot_type == "1D":
            ax = fig.add_subplot(111)
            if dim_data.get(dim1) and len(dim_data[dim1]) == len(red_percents):
                paired = sorted(zip(dim_data[dim1], red_percents))
                sorted_xs = [p[0] for p in paired]
                sorted_rs = [p[1] for p in paired]
                ax.plot(sorted_xs, sorted_rs, marker='o', linestyle='-', color='b')
                ax.set_xlabel(f'Stepper {dim1} Location')
            else:
                ax.plot(red_percents, marker='o', linestyle='-', color='b')
                ax.set_xlabel('Index')
            ax.set_ylabel('Red Percent')
            ax.set_title(f'Red Percent vs {dim1}')
            ax.grid(True)
        elif plot_type == "2D":
            ax = fig.add_subplot(111, projection='3d')
            x, y, z = dim_data[dim1], dim_data[dim2], red_percents
            if len(x) == len(z) and len(y) == len(z):
                scatter = ax.scatter(x, y, z, c=z, cmap='coolwarm', marker='o')
                ax.set_xlabel(f'Stepper {dim1}')
                ax.set_ylabel(f'Stepper {dim2}')
                ax.set_zlabel('Red Percent')
                fig.colorbar(scatter, ax=ax, label='Red Percent')
        elif plot_type == "3D":
            ax = fig.add_subplot(111, projection='3d')
            x, y, z, c = dim_data[dim1], dim_data[dim2], dim_data[dim3], red_percents
            if len(x) == len(c) and len(y) == len(c) and len(z) == len(c):
                scatter = ax.scatter(x, y, z, c=c, cmap='coolwarm', marker='o')
                ax.set_xlabel(f'Stepper {dim1}')
                ax.set_ylabel(f'Stepper {dim2}')
                ax.set_zlabel(f'Stepper {dim3}')
                fig.colorbar(scatter, ax=ax, label='Red Percent')
                
        self.canvas = FigureCanvasQTAgg(fig)
        self.toolbar = NavigationToolbar2QT(self.canvas, self)
        
        self.plot_frame.addWidget(self.toolbar)
        self.plot_frame.addWidget(self.canvas)


class RedPercentDynamicView(QtDynamicView):
    def __init__(self, model, parent=None):
        super().__init__(model, parent)
        self._add_sync_dimension_controls()
        
    def _add_sync_dimension_controls(self):
        sync_frame = QFrame()
        sync_layout = QHBoxLayout(sync_frame)
        lbl = QLabel("Sync Dimensions:")
        lbl.setProperty("class", "header")
        sync_layout.addWidget(lbl)
        
        self.sync_cbs = {}
        for dim in ['X', 'Y', 'Z']:
            cb = QCheckBox(dim)
            cb.setChecked(dim in self.model.sync_dimensions)
            cb.stateChanged.connect(self._update_sync_dimensions)
            self.sync_cbs[dim] = cb
            sync_layout.addWidget(cb)
        sync_layout.addStretch()
        self.layout.insertWidget(self.layout.count() - 1, sync_frame)

    def _update_sync_dimensions(self):
        self.model.sync_dimensions = [dim for dim, cb in self.sync_cbs.items() if cb.isChecked()]

    def _execute_command(self, cmd_name):
        if cmd_name == "select_focus_area":
            self.overlay = SelectionOverlay(self.model)
            self.overlay.show()
        elif cmd_name == "plot_data_ui":
            if hasattr(self, 'plot_dialog') and self.plot_dialog:
                self.plot_dialog.deleteLater()
            self.plot_dialog = PlotDialog(self)
            self.plot_dialog.show()
        elif cmd_name == "save_log":
            self.save_log_ui()
        elif cmd_name == "stop_monitoring":
            super()._execute_command(cmd_name)
            if self.model.data_log and self.model.data_log.red_values:
                reply = QMessageBox.question(
                    self, 
                    "Save Log", 
                    "Monitoring stopped. Would you like to save the data to a CSV?", 
                    QMessageBox.Yes | QMessageBox.No
                )
                if reply == QMessageBox.Yes:
                    self.save_log_ui()
        else:
            super()._execute_command(cmd_name)

    def save_log_ui(self):
        if not self.model.data_log or not self.model.data_log.red_values:
            QMessageBox.information(self, "No Data", "No data to save.")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Red Detection Log", "", "CSV Files (*.csv);;All Files (*)"
        )
        if file_path:
            try:
                self.model.data_log.save_to_csv(file_path)
                print(f"[color_test] Log saved to: {file_path}")
            except Exception as e:
                from error_routing import ErrorRouter
                msg = f"[color_test] Error saving file: {e}"
                print(msg)
                ErrorRouter.report_error("File Save Error", msg, e)

    def cleanup(self):
        super().cleanup()
        if hasattr(self.model, 'stop_monitoring'):
            self.model.stop_monitoring()
        if hasattr(self, 'plot_dialog') and self.plot_dialog:
            self.plot_dialog.close()


class DashboardWindow(QMainWindow):
    def __init__(self, system_manager):
        super().__init__()
        self.system_manager = system_manager
        self.setWindowTitle("Unified Control Dashboard (PySide6)")
        self.resize(1200, 800)
        self.setDockOptions(QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks)
        
        self.setStyleSheet("""
            QMainWindow::separator { width: 4px; background: #333; }
            QMainWindow::separator:hover { background: #0078D4; }
        """)
        
        # Sidebar
        self.sidebar = QDockWidget("Device Manager", self)
        self.sidebar.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.sidebar.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.device_list = QListWidget()
        self.device_list.setStyleSheet("background-color: #1E1E1E; color: white; border: none;")
        self.sidebar.setWidget(self.device_list)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.sidebar)
        
        self.device_list.itemClicked.connect(self.on_device_clicked)
        
        self.setCentralWidget(QWidget()) # Empty workspace
        
        self.active_docks = {}
        self._last_added_dock = None
        self.populate_sidebar()

    def populate_sidebar(self):
        self.device_list.clear()
        for name in self.system_manager.active_models.keys():
            item = QListWidgetItem(name)
            self.device_list.addItem(item)
            # Auto-open all devices initially
            self.open_device_view(name)

    def on_device_clicked(self, item):
        self.open_device_view(item.text())

    def open_device_view(self, device_name):
        if device_name in self.active_docks:
            dock = self.active_docks[device_name]
            dock.show()
            dock.raise_()
            dock.activateWindow()
            return
            
        model = self.system_manager.get_model(device_name)
        if not model:
            return
            
        dock = QDockWidget(device_name, self)
        dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        
        if device_name == "Red Percent Window":
            view_widget = RedPercentDynamicView(model)
        else:
            view_widget = QtDynamicView(model)
        dock.setWidget(view_widget)
        
        dock.visibilityChanged.connect(lambda visible: self.on_dock_closed(device_name, visible))
        
        if self._last_added_dock:
            self.splitDockWidget(self._last_added_dock, dock, Qt.Horizontal)
        else:
            self.addDockWidget(Qt.RightDockWidgetArea, dock)
            
        self._last_added_dock = dock
        self.active_docks[device_name] = dock
        
    def on_dock_closed(self, device_name, visible):
        if not visible and device_name in self.active_docks:
            # Do not delete the dock; keep it cached so it can be restored from the sidebar
            pass

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
