import re
with open('src/controller/gamepad.py', 'r') as f:
    content = f.read()

# Fix OS watchdog
old_os = '''        if sys.platform.startswith("linux"):
            return os.path.exists(f"/dev/input/js{self.controller_index}")
        elif sys.platform == "win32":
            info = JOYINFOEX()
            info.dwSize = ctypes.sizeof(JOYINFOEX)
            info.dwFlags = 255
            return ctypes.windll.winmm.joyGetPosEx(self.controller_index, ctypes.byref(info)) == 0
        return True'''
new_os = '''        if sys.platform.startswith("linux"):
            return os.path.exists(f"/dev/input/js{self.controller_index}")
        elif sys.platform == "win32":
            info = JOYINFOEX()
            info.dwSize = ctypes.sizeof(JOYINFOEX)
            info.dwFlags = 255
            return ctypes.windll.winmm.joyGetPosEx(self.controller_index, ctypes.byref(info)) == 0
        elif sys.platform == "darwin":
            if not getattr(self, 'gamepad', None) or not getattr(self.gamepad, 'joystick', None):
                return False
            try:
                self.gamepad.joystick.get_name()
                return True
            except:
                return False
        return True'''
content = content.replace(old_os, new_os)

# Fix polling loop re-entrancy and crash
old_poll = '''    def _poll_loop(self):
        if not self.is_polling: return

        if self.activity_callback:
            self.activity_callback()

        # Try to pump Pygame events
        if self.gamepad:
            pygame.event.get()
            try:
                for i in range(self.gamepad.joystick.get_numaxes()): # type: ignore'''
new_poll = '''    def _poll_loop(self):
        if not self.is_polling: return
        if getattr(self, '_in_poll_loop', False): return
        self._in_poll_loop = True

        if self.activity_callback:
            self.activity_callback()

        # Try to pump Pygame events
        if self.gamepad:
            try:
                pygame.event.get()
                for i in range(self.gamepad.joystick.get_numaxes()): # type: ignore'''

content = content.replace(old_poll, new_poll)

old_end_poll = '''            except pygame.error:
                message = "[controllerDrive] pygame.error detected. Simulating gamepad disconnect..."
                if hasattr(self, 'log_updater') and callable(self.log_updater):
                    self.log_updater(message)
                else: print(f"[controllerDrive] {message}")

        if not self._is_os_connected():
            self._handle_disconnect()
            return
            
        if self.gui:
            self.gui.after(50, self._poll_loop)'''
new_end_poll = '''            except pygame.error:
                message = "[controllerDrive] pygame.error detected. Simulating gamepad disconnect..."
                if hasattr(self, 'log_updater') and callable(self.log_updater):
                    self.log_updater(message)
                else: print(f"[controllerDrive] {message}")

        if not self._is_os_connected():
            self._handle_disconnect()
            self._in_poll_loop = False
            return
            
        if self.gui:
            self.gui.after(50, self._poll_loop)
        self._in_poll_loop = False'''

content = content.replace(old_end_poll, new_end_poll)

with open('src/controller/gamepad.py', 'w') as f:
    f.write(content)
