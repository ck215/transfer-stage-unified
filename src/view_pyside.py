import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QDockWidget, QListWidget, QWidget, 
    QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QFrame, QMessageBox, QListWidgetItem
)
from PySide6.QtCore import Qt, QTimer

class QtDynamicView(QWidget):
    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.poll_interval_ms = 50
        self.layout = QVBoxLayout(self)
        self.vars = {}  # attr -> QLineEdit/QLabel
        self.toggle_buttons = []
        
        self.setStyleSheet("""
            QWidget { background-color: #121212; color: #FFFFFF; font-family: 'Segoe UI', sans-serif; }
            QLabel.header { font-weight: bold; color: #0078D4; margin-top: 10px; }
            QLineEdit { background-color: #1E1E1E; border: 1px solid #333; padding: 5px; color: #FFF; }
            QPushButton { background-color: #0078D4; color: white; font-weight: bold; padding: 5px; border-radius: 3px; }
            QPushButton:hover { background-color: #107C10; }
            QFrame { background-color: #1E1E1E; border-radius: 5px; }
        """)
        
        self._build_ui()
        
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_model)
        self.timer.start(self.poll_interval_ms)

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
                            return lambda: setattr(self.model, attr_name, widget.text())
                            
                        val_widget.editingFinished.connect(make_editor(attr, val_widget))
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
                    
                card_layout.addLayout(row_layout)
            self.layout.addWidget(card)
        self.layout.addStretch()

    def _execute_command(self, cmd_name):
        func = getattr(self.model, cmd_name, None)
        if func and callable(func):
            try:
                func()
            except Exception as e:
                print(f"Command {cmd_name} failed: {e}")

    def _poll_model(self):
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
            if val:
                if widget.text() != tb["true_text"]:
                    widget.setText(tb["true_text"])
                    widget.setStyleSheet("background-color: #107C10; color: white;")
            else:
                if widget.text() != tb["false_text"]:
                    widget.setText(tb["false_text"])
                    widget.setStyleSheet("background-color: #D13438; color: white;")


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
        self.device_list = QListWidget()
        self.device_list.setStyleSheet("background-color: #1E1E1E; color: white; border: none;")
        self.sidebar.setWidget(self.device_list)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.sidebar)
        
        self.device_list.itemClicked.connect(self.on_device_clicked)
        
        self.setCentralWidget(QWidget()) # Empty workspace
        
        self.active_docks = {}
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
            self.active_docks[device_name].raise_()
            return
            
        model = self.system_manager.get_model(device_name)
        if not model:
            return
            
        dock = QDockWidget(device_name, self)
        dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        
        # In the future, route to bespoke views if model.custom_view_class exists.
        # For now, DynamicView handles all.
        view_widget = QtDynamicView(model)
        dock.setWidget(view_widget)
        
        dock.visibilityChanged.connect(lambda visible: self.on_dock_closed(device_name, visible))
        
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.active_docks[device_name] = dock
        
    def on_dock_closed(self, device_name, visible):
        if not visible and device_name in self.active_docks:
            # Clean up the dock when closed to allow relaunching
            self.active_docks[device_name].deleteLater()
            del self.active_docks[device_name]
            
    def closeEvent(self, event):
        self.system_manager.shutdown_all()
        event.accept()
