import traceback
import time

class ErrorRouter:
    _error_cb = None
    _warning_cb = None
    _info_cb = None
    
    _last_messages = {}

    @classmethod
    def set_callbacks(cls, err, warn, info):
        cls._error_cb = err
        cls._warning_cb = warn
        cls._info_cb = info
        
    @classmethod
    def _is_spam(cls, message):
        message = str(message)
        now = time.time()
        if message in cls._last_messages:
            if now - cls._last_messages[message] < 5.0:
                return True
        cls._last_messages[message] = now
        # Keep dict small
        if len(cls._last_messages) > 100:
            cls._last_messages = {m: t for m, t in cls._last_messages.items() if now - t < 5.0}
        return False

    @classmethod
    def report_error(cls, title, message, exception=None):
        if cls._is_spam(message): return
        if cls._error_cb:
            cls._error_cb(title, message, exception)
        else:
            print(f"[ERROR] {title}: {message}")
            if exception: traceback.print_exc()

    @classmethod
    def report_warning(cls, title, message, exception=None):
        if cls._is_spam(message): return
        if cls._warning_cb:
            cls._warning_cb(title, message, exception)
        else:
            print(f"[WARNING] {title}: {message}")

    @classmethod
    def report_info(cls, title, message):
        if cls._is_spam(message): return
        if cls._info_cb:
            cls._info_cb(title, message)
        else:
            print(f"[INFO] {title}: {message}")
