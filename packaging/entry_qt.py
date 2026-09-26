"""`station-qt` in the PyInstaller bundle: the PySide6 view, nothing else.

PySide6 plugin discovery is the classic failure inside a bundle
(PACKAGING_PLAN, Risks): Qt looks for its platform plugin (`cocoa`,
`windows`, `xcb`) relative to the Qt libraries, and when it finds none it
aborts natively with no Python traceback. So this entry point, before
anything imports Qt:

1. points `QT_QPA_PLATFORM_PLUGIN_PATH` at the bundle's own
   `PySide6/Qt/plugins/platforms` (unless the caller already set one);
2. writes the paths it resolved to stderr at once, and to the station's log
   file as soon as `app.launch()` opens it (an `events.debug` line under
   source `packaging`), so a failed start on a lab machine says where Qt
   looked.

Running from source (not frozen) it changes nothing and only logs.
"""
import os
import sys


def _bundle_plugin_dirs():
    """(plugins, platforms) inside the bundle, or (None, None) from source."""
    base = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or base is None:
        return None, None
    # macOS and Linux wheels keep Qt under PySide6/Qt/; Windows under PySide6/.
    plugins = next((p for p in (os.path.join(base, "PySide6", "Qt", "plugins"),
                                os.path.join(base, "PySide6", "plugins"))
                    if os.path.isdir(p)), None)
    if plugins is None:
        return None, None
    platforms = os.path.join(plugins, "platforms")
    return (plugins if os.path.isdir(plugins) else None,
            platforms if os.path.isdir(platforms) else None)


def _set_plugin_path():
    plugins, platforms = _bundle_plugin_dirs()
    if platforms and not os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH"):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = platforms
    listing = sorted(os.listdir(platforms)) if platforms else []
    return {"frozen": bool(getattr(sys, "frozen", False)),
            "QT_QPA_PLATFORM_PLUGIN_PATH":
                os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH", "(unset)"),
            "QT_PLUGIN_PATH": os.environ.get("QT_PLUGIN_PATH", "(unset)"),
            "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "(default)"),
            "bundle_plugins": plugins or "(not frozen)",
            "platform_plugins": ",".join(listing) or "(none found)"}


def _qt_library_plugins():
    """Where Qt itself says its plugins are (QLibraryInfo), or the error."""
    try:
        from PySide6.QtCore import QLibraryInfo
        return QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
    except Exception as exc:        # reported, never fatal here
        return f"(QLibraryInfo failed: {exc})"


def _log_when_the_log_opens(text):
    """Write `text` to the station log the moment `launch()` opens it.

    The log file does not exist yet at this point (`app.launch` opens it),
    so the line rides on `events.open_file`; a line logged before it would
    reach the in-memory log only.
    """
    from events import events
    open_file = events.open_file

    def open_file_then_log(*args, **kwargs):
        path = open_file(*args, **kwargs)
        events.debug("Qt Plugin Path", text, source="packaging")
        return path

    events.open_file = open_file_then_log


def main():
    resolved = _set_plugin_path()
    resolved["QLibraryInfo.PluginsPath"] = _qt_library_plugins()
    text = " ".join(f"{k}={v}" for k, v in resolved.items())
    print(f"station-qt: Qt Plugin Path: {text}", file=sys.stderr, flush=True)
    _log_when_the_log_opens(text)
    from app import main_qt
    return main_qt()


if __name__ == "__main__":
    sys.exit(main())
