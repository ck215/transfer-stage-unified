import re

with open('src/view_pyside.py', 'r') as f:
    content = f.read()

# Fix timeout 5000 -> 300000
content = content.replace('self.disable_timer.start(5000)', 'self.disable_timer.start(300000)')

# Fix memory leaks by deleting old plot and log windows
content = content.replace('self.plot_dialog = PlotDialog(self)', '''
            if hasattr(self, 'plot_dialog') and self.plot_dialog:
                self.plot_dialog.deleteLater()
            self.plot_dialog = PlotDialog(self)
''')

# Wait, log window is already handled by:
# if not hasattr(self, 'log_window') or not self.log_window.isVisible():
# But to prevent leak if hidden, we can just do deleteLater on closeEvent, 
# or use Qt.WA_DeleteOnClose.
# Let's add Qt.WA_DeleteOnClose to ControllerLogWindow
content = content.replace('self.setWindowTitle("Controller Log Window")', 'self.setWindowTitle("Controller Log Window")\\n        self.setAttribute(Qt.WA_DeleteOnClose)')

# Fix Tkinter mixed API
content = content.replace('QMessageBox.showerror', 'QMessageBox.critical')

with open('src/view_pyside.py', 'w') as f:
    f.write(content)
