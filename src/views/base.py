"""Toolkit-neutral view logic, written once for Tk and Qt.

`Dashboard` is the window: model tabs, the global FULL STOP toggle, event
popups, opening and closing models. `PanelView` is one panel rendered from a
schema. Both hold the Controller and nothing else from the backend.

A toolkit subclass supplies widgets only. Every `_make_<element type>` is
required, so a view that cannot render an element type cannot be built -
which is how feature parity is enforced rather than hoped for.
"""
import time

import schema as sch
from events import events


class PanelView:
    REFRESH_MS = 100
    #: Minimum ms between re-running a data command, by element type. A plot
    #: series is cheap; an `image` (a rendered figure) is not.
    DATA_REFRESH_MS = {"plot": 0, "log_stream": 0, "image": 1000}

    def __init__(self, controller, name, panel=None):
        """`panel` is given only for panels the Controller does not own (Setup)."""
        self.controller, self.name, self._panel = controller, name, panel
        missing = [t for t in sorted(sch.ELEMENT_TYPES)
                   if not callable(getattr(self, f"_make_{t}", None))]
        if missing:
            raise TypeError(f"{type(self).__name__} cannot render: {', '.join(missing)}")
        self._elements = []     # every built element, for refresh and gating
        self._data_last = {}    # id(element) -> monotonic time of the last data call

    # -- the three calls a view makes -------------------------------------
    def _schema(self):
        return self._panel.schema if self._panel else self.controller.schema(self.name)

    def _state(self):
        return self._panel.state if self._panel else self.controller.state(self.name)

    def _call(self, command, inputs=None, args=()):
        if self._panel:
            return self._panel.run(command, inputs, args)
        return self.controller.run(self.name, command, inputs, args)

    def _options(self, command):
        return self._panel.options(command) if self._panel else self.controller.options(self.name, command)

    # -- build -------------------------------------------------------------
    def _build(self):
        for section in self._schema()["sections"]:
            container = self._make_section(section["title"],
                                           section.get("layout", "column"))
            for element in section["elements"]:
                getattr(self, f"_make_{element['type']}")(container, element)
                self._elements.append(element)
        self._apply_theme()
        self._refresh()

    # -- run ---------------------------------------------------------------
    def _gather_inputs(self):
        """Every writable entry's current text travels with every command, so
        a value typed a moment ago is never one edit behind."""
        return {e["model_attr"]: self._read_entry(e) for e in self._elements
                if e["type"] == "entry" and e.get("writable")}

    def _run(self, element, args=()):
        command = element.get("command")
        args = tuple(element.get("args") or ()) + tuple(args)
        result = self._call(command, self._gather_inputs(), tuple(args))
        if result.needs_confirm and self._confirm(result.reason):
            result = self._call(result.command, result.inputs, (*result.args, True))
        if result.is_refused:
            self._show_refused(result.reason)
        elif result.is_ok:
            self._show_refused("")
        self._refresh()
        return result            # failed: already an acknowledged event

    def _run_toggle(self, element):
        is_on = bool(self._state()["values"].get(element["model_attr"]))
        return self._run(element, element["off_args"] if is_on else element["on_args"])

    # -- refresh -----------------------------------------------------------
    def _refresh(self):
        state = self._state()
        values, mode = state["values"], state["mode"]
        for element in self._elements:
            kind, attr = element["type"], element.get("model_attr")
            if kind == "entry":
                if not self._entry_is_dirty(element):
                    self._set_text(element, values.get(attr, ""))
            elif kind in ("readonly", "region_select", "dropdown") and attr:
                self._set_text(element, values.get(attr, ""))
            elif kind in ("toggle", "indicator"):
                self._set_on(element, bool(values.get(attr)))
            elif kind in ("plot", "image", "log_stream"):
                if self._data_is_due(element, kind):
                    key = "source_command" if kind == "log_stream" else "data_command"
                    data = self._call(element[key])
                    if data.is_ok:
                        self._set_data(element, data.value)
            self._set_enabled(element, sch.is_enabled(element, mode))
        age = state.get("age")
        self._set_stale(age is not None and age > 1.0)

    def _data_is_due(self, element, kind):
        interval = self.DATA_REFRESH_MS.get(kind, 0) / 1000.0
        now = time.monotonic()
        if now - self._data_last.get(id(element), 0.0) < interval:
            return False
        self._data_last[id(element)] = now
        return True

    _sync_gates = _refresh   # gating is part of every refresh, entries included

    def close(self):
        self._elements.clear()

    # -- a toolkit subclass supplies these ---------------------------------
    def _make_section(self, title, layout="column"): raise NotImplementedError
    def _read_entry(self, element): raise NotImplementedError
    def _entry_is_dirty(self, element): raise NotImplementedError
    def _set_text(self, element, text): raise NotImplementedError
    def _set_on(self, element, is_on): raise NotImplementedError      # colours: theme.toggle_colors
    def _set_data(self, element, data): raise NotImplementedError
    def _set_enabled(self, element, is_enabled): raise NotImplementedError
    def _set_stale(self, is_stale): raise NotImplementedError
    def _confirm(self, prompt): raise NotImplementedError             # -> bool
    def _show_refused(self, reason): raise NotImplementedError        # non-modal status line
    def _apply_theme(self): raise NotImplementedError


class Dashboard:
    def __init__(self, controller, setup):
        self.controller, self.setup = controller, setup
        self._closing = False
        self._launched = False

    def open(self):
        events.subscribe(self._on_event)
        self.controller.subscribe(self._on_models_changed)

    def close(self):
        """Unsubscribe FIRST: a popup opened from inside a close path blocks
        the exit (it hung the Qt suite for three sessions)."""
        self._closing = True
        events.unsubscribe(self._on_event)
        self.controller.unsubscribe(self._on_models_changed)
        self.controller.close()

    def open_model(self, name):
        return self.controller.reopen(name)

    def close_model(self, name):
        return self.controller.remove(name)

    def toggle_estop_all(self):
        if not self.controller.is_estopped:
            return self.controller.estop_all()
        result = self.controller.clear_estop_all()
        if result.needs_confirm and self._confirm(result.reason):
            result = self.controller.clear_estop_all(confirmed=True)
        return result

    def _on_event(self, event):
        """Any thread. Everything goes to the log panel; only `needs_ack`
        events become a popup, and never while closing."""
        if self._closing:
            return
        def show():
            self._show_event(event)
            if event.needs_ack and not self._closing:
                self._show_popup(event)
        self._marshal(show)

    def _on_models_changed(self, change, name):
        def apply():
            if change == "added":
                self._add_panel(name)
                if not self._launched:
                    self._launched = True
                    self._collapse_setup()   # the wizard gives way to the models
            else:
                self._remove_panel(name)
        self._marshal(apply)

    def _on_focus_change(self, is_focused):
        self.controller.set_input_focus(is_focused)

    # -- a toolkit subclass supplies these ---------------------------------
    def _marshal(self, fn): raise NotImplementedError      # run fn on the UI thread
    def _show_event(self, event): raise NotImplementedError
    def _show_popup(self, event): raise NotImplementedError
    def _confirm(self, prompt): raise NotImplementedError
    def _add_panel(self, name): raise NotImplementedError
    def _remove_panel(self, name): raise NotImplementedError
    def _collapse_setup(self): pass          # minimise the Setup panel; reopenable
