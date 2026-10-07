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


def join_names(names):
    """'A', 'A and B', 'A, B and C' - station order, never sorted."""
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def event_line(event):
    """The one wording of an event in a tray, a band or a log (round 7,
    L11): the title in sentence case, a colon, the message, the repeat
    count; never the `[source]` prefix (the source is the log file's
    business). Takes an Event or its dict."""
    get = (lambda k, d=None: event.get(k, d)) if isinstance(event, dict) else (
        lambda k, d=None: getattr(event, k, d))
    title = str(get("title") or "").strip()
    if title and not (len(title) > 1 and title[1:].islower() and title[0].isupper()):
        title = title[:1].upper() + title[1:].lower()
    message = str(get("message") or "").strip()
    count = int(get("count") or 1)
    line = f"{title}: {message}" if title and message else (title or message)
    return f"{line} (x{count})" if count > 1 else line


#: The device class names the views treated as hardware links before a
#: model published `hardware_devices` (MOD-5). Read ONLY for a state that
#: lacks the key; never when the key is there, even as an empty list.
LEGACY_LINK_DEVICES = ("SerialPort", "SMC100")


def hardware_links(state, fallback=LEGACY_LINK_DEVICES):
    """`{device: status}` for the hardware links in one model's state.

    `state["hardware_devices"]` names them: the class names of the model's
    devices whose `Device.is_hardware` is True (MOD-5 / CON-6), so a new
    link (a `PiezoLink`) counts without a view learning its name. A state
    WITHOUT the key comes from a model older than that list and falls back
    to `fallback` class names, or to every device when `fallback` is None
    (Qt's rule before MOD-5, which keyed on status words). A present but
    empty list means "no hardware links" and never falls back.
    """
    state = state or {}
    devices = state.get("devices") or {}
    if "hardware_devices" in state:
        names = set(state.get("hardware_devices") or ())
    elif fallback is None:
        return dict(devices)
    else:
        names = set(fallback)
    return {device: status for device, status in devices.items() if device in names}


# -- the link (F3 / HC-1, rb-link-views) --------------------------------------
#
# A model that owns a hardware serial port publishes `state["link"]`:
#   {"status": "verified" | "unverified" | "simulated" | "lost" |
#              "reconnecting" | "closed" | "connecting",
#    "losses": int, "reconnects": int, "dropped": int, "stalls": int,
#    "stalled": bool, "last_loss": "HH:MM:SS" | None}
# The key is absent for a model without a port. Everything a view says about
# it is decided here, once, from the state alone; no model is named.

#: The link statuses under which the model's numbers are not live and its
#: modes are held: the entry turns the danger tier.
LINK_DOWN = ("lost", "reconnecting")
#: The statuses under which "stalled" is news: the link itself is up.
LINK_UP = ("verified", "unverified", "simulated")
#: The commands that ARE the stop; a down link never greys them.
STOP_COMMANDS = ("toggle_estop", "clear_estop", "estop", "estop_all",
                 "clear_estop_all")


def link_of(state):
    """The model's `link` dict, or None when it has no port."""
    link = (state or {}).get("link") if isinstance(state, dict) else None
    return link if isinstance(link, dict) else None


def link_is_down(state):
    """True while the model's link is lost or reconnecting."""
    link = link_of(state)
    return bool(link) and str(link.get("status") or "") in LINK_DOWN


def _stall_seconds(state):
    """How long the model has had no position, in whole seconds, from the
    reading the model already publishes (`position_age`, else its `age`)."""
    values = (state or {}).get("values") or {}
    for raw in (values.get("position_age"), (state or {}).get("age")):
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            continue
        if seconds >= 0:
            return int(round(seconds))
    return None


def link_notice(state):
    """(severity, line) for the link, in operator words; ("", "") when there
    is nothing to say. Severity is `theme.SEVERITY_*`'s vocabulary: "error"
    is the danger tier (the signal rule), "warning" the attention tier."""
    link = link_of(state)
    if not link:
        return "", ""
    status = str(link.get("status") or "")
    at = str(link.get("last_loss") or "").strip()
    lost = f"Link lost {at}" if at else "Link lost"
    if status == "reconnecting":
        return "error", f"{lost}, reconnecting…"
    if status == "lost":
        return "error", f"{lost}; not reconnecting"
    if link.get("stalled") and status in LINK_UP:
        seconds = _stall_seconds(state)
        span = f" for {seconds} s" if seconds is not None else ""
        return "warning", f"No position{span}; link up, check the board"
    return "", ""


def link_gate_reason(state):
    """Why a down link greys a mode control, or ""."""
    link = link_of(state)
    status = str((link or {}).get("status") or "")
    if status == "reconnecting":
        return "Link lost: wait for it to reconnect"
    if status == "lost":
        return "Link lost: press Stop, check the cable, then relaunch from Setup"
    return ""


def is_stop_control(element):
    """The stop itself (a per-model stop toggle, a stop button)."""
    return bool(element.get("stop")) or element.get("command") in STOP_COMMANDS


def link_holds(element):
    """Whether a down link greys `element`: a mode toggle or a `go` command,
    never the stop. Read from the element's own type and role."""
    if is_stop_control(element):
        return False
    return element.get("type") == "toggle" or element.get("role") == "go"


def entry_notices(state):
    """Every standing line an entry shows under its head, worst first:
    [(severity, line), ...]."""
    notices = [n for n in (link_notice(state),) if n[1]]
    return sorted(notices, key=lambda n: SEVERITY_RANK.get(n[0], 9))


#: Worst first.
SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def worst_severity(notices):
    """The tier an entry's rule and its rail line take: "error", "warning"
    or ""."""
    severities = [n[0] for n in notices or () if n and n[0]]
    return min(severities, key=lambda s: SEVERITY_RANK.get(s, 9)) if severities else ""


#: Why a control is greyed, by the mode word that greys it, in two
#: directions (round 8, IMP8-1: Web and Tk said "Not in manual mode" while the
#: probe WAS in manual mode). Index 0: the mode is in the element's
#: `disabled_when`; index 1: the mode is missing from its `enabled_when`.
GATE_WORDS = {
    "latched": ("Stopped: clear the stop first", None),
    "manual": ("In manual mode", "Not in manual mode"),
    "autonomous": ("In autonomous mode", "Not in autonomous mode"),
    "idle": ("Idle", "Enter a mode first"),
    "disabled": ("Not in a mode", "Enter a mode first"),
    "fault": ("Faulted: clear the fault first", None),
    "moving": ("Moving", None),
    "running": ("A run is in progress", "No run in progress"),
    "no_region": ("Set a capture region first", None),
    "disconnected": ("Not connected", None),
    "stale": ("Readings are stale", None),
    "scanning": ("Scanning ports", None),
    # The Transfer Map (Tier S): a trial is armed until Finish or Abort.
    "armed": ("A trial is armed: finish or abort it first", "Arm a trial first"),
    "ready": (None, "Nothing to launch yet"),
    # The Sample Map (flake-coords section 5.3): a control that needs the
    # chip's frame, or a locating axis to read.
    "unregistered": ("Mark corners A and B first", None),
    "no_source": ("No locating axes", None),
    # The Rotator turns the chip (owner 2026-10-04): no angle, no marks.
    "rotator_unknown": ("Rotator angle unknown: home or reconnect it", None),
    "launched": (None, "Nothing launched yet"),
}


def gate_reason(element, mode_name):
    """The sentence a view puts on a disabled control, or "" when `element`
    is not gated by `mode_name`. Reads the element's own gate lists, so the
    direction is never guessed from the word alone."""
    mode = str(mode_name or "")
    disabled_when = list(element.get("disabled_when") or ())
    enabled_when = list(element.get("enabled_when") or ())
    if mode in disabled_when:
        words = GATE_WORDS.get(mode, (None, None))[0]
        return words or f"In {mode} mode"
    if enabled_when and mode not in enabled_when:
        wanted = str(enabled_when[0])
        words = GATE_WORDS.get(wanted, (None, None))[1]
        return words or f"Not in {wanted} mode"
    return ""


def stop_words(stop_state):
    """What every view says about the stop, from `Controller.stop_state`
    (round 7: IMP7-1/2, TK7-1, QT7-1). One function, three views, so the
    disc, the headline, its subline and the rail line never disagree.

    - `face` / `action`: the disc reads "Clear" (and a press clears) only
      while EVERY model is latched; otherwise it reads "Stop" and a press is
      `estop_all`, so one model's own switch never takes the stop away from
      the five that are live.
    - `headline`: "Every model is stopped." only when every model is latched
      AND every one confirmed; when some did not, the headline names them.
      A partial stop has no headline.
    - `rail`: the line under the disc: the names of a partial stop, or the
      models that did not confirm.
    """
    latched = list(stop_state.get("latched") or [])
    unconfirmed = list(stop_state.get("unconfirmed") or [])
    every = bool(stop_state.get("every")) and bool(latched)
    plural = len(unconfirmed) > 1
    words = {"face": "Stop", "action": "stop", "headline": "", "subline": "", "rail": ""}
    if not latched:
        return words
    if every:
        words["face"], words["action"] = "Clear", "clear"
    if unconfirmed:
        names = join_names(unconfirmed)
        words["rail"] = f"Stopped: {names} did not confirm"
        if every:
            words["headline"] = f"Stopped. {names} did not confirm."
            words["subline"] = (f"Treat {'them' if plural else 'it'} as live until you "
                                f"have checked {'them' if plural else 'it'} by hand.")
    elif every:
        words["headline"] = "Every model is stopped."
        words["rail"] = "Stopped: every model latched"
    else:
        words["rail"] = f"Stopped: {join_names(latched)}"
    return words


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
        #: What the entry says under its head now: [(severity, line)], worst
        #: first (`entry_notices`), and whether its link is down.
        self.notices = []
        self.link_down, self._link_reason = False, ""

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
    def _gather_inputs(self, element):
        """What travels with `element`'s command (MOD-6 / CON-8): the
        entries it declares in `inputs`, edited or not, so they are
        validated as a set (D-5); plus every writable entry the operator
        has edited and not committed (`_entry_is_edited`), so a value typed
        a moment ago is never one edit behind and never lost. A clean entry
        the command does not declare stays home: a stale or bad box
        elsewhere cannot refuse an unrelated command."""
        declared = set((element or {}).get("inputs") or ())
        return {e["model_attr"]: self._read_entry(e) for e in self._elements
                if e["type"] == "entry" and e.get("writable")
                and (e["model_attr"] in declared or self._entry_is_edited(e))}

    def _entry_is_edited(self, element):
        """The box holds text the operator typed and has not committed.
        Defaults to `_entry_is_dirty`; a toolkit whose dirty rule also
        counts focus (refresh protection) overrides this with the text
        comparison alone, because focus is not an edit."""
        return self._entry_is_dirty(element)

    def _run(self, element, args=()):
        command = element.get("command")
        args = tuple(element.get("args") or ()) + tuple(args)
        result = self._call(command, self._gather_inputs(element), tuple(args))
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

    def _run_checkbox(self, element):
        """A tick sends the NEW value, read from the model rather than the
        widget, so a box that was drawn one refresh behind still flips the
        right way."""
        is_on = bool(self._state()["values"].get(element["model_attr"]))
        return self._run(element, (not is_on,))

    # -- refresh -----------------------------------------------------------
    def _refresh(self):
        state = self._state()
        values, mode = state["values"], state["mode"]
        # F3 / HC-1: a lost or reconnecting link holds the mode controls and
        # the `go` commands (never the stop) and mutes the numbers.
        down = link_is_down(state)
        self.link_down, self._link_reason = down, link_gate_reason(state)
        for element in self._elements:
            kind, attr = element["type"], element.get("model_attr")
            if kind == "entry":
                if not self._entry_is_dirty(element):
                    self._set_text(element, values.get(attr, ""))
            elif kind in ("readonly", "region_select", "dropdown") and attr:
                self._set_text(element, values.get(attr, ""))
            elif kind in ("toggle", "indicator", "checkbox"):
                self._set_on(element, bool(values.get(attr)))
            elif kind in ("plot", "image", "log_stream"):
                if self._wants_data(element) and self._data_is_due(element, kind):
                    key = "source_command" if kind == "log_stream" else "data_command"
                    data = self._call(element[key])
                    if data.is_ok:
                        self._set_data(element, data.value)
            is_enabled = sch.is_enabled(element, mode, values)
            if down and is_enabled and link_holds(element):
                is_enabled = False
            self._set_enabled(element, is_enabled)
        age = state.get("age")
        self._set_stale((age is not None and age > 1.0) or down)
        self.notices = entry_notices(state)
        self._set_notices(self.notices)

    def _data_is_due(self, element, kind):
        interval = self.DATA_REFRESH_MS.get(kind, 0) / 1000.0
        now = time.monotonic()
        if now - self._data_last.get(id(element), 0.0) < interval:
            return False
        self._data_last[id(element)] = now
        return True

    _sync_gates = _refresh   # gating is part of every refresh, entries included

    def _wants_data(self, element):
        """Whether a data element should be polled now. A detached log
        stream (G4) is polled only while its window is open; a toolkit
        overrides this to say so. Everything else is always wanted."""
        return True

    def close(self):
        self._elements.clear()

    # -- a toolkit subclass supplies these ---------------------------------
    def _make_section(self, title, layout="column"): raise NotImplementedError
    def _read_entry(self, element): raise NotImplementedError
    def _entry_is_dirty(self, element): raise NotImplementedError
    # _entry_is_edited(element) -> bool: optional; see above
    def _set_text(self, element, text): raise NotImplementedError
    def _set_on(self, element, is_on): raise NotImplementedError      # colours: theme.toggle_colors
    def _set_data(self, element, data): raise NotImplementedError
    def _set_enabled(self, element, is_enabled): raise NotImplementedError
    def _set_stale(self, is_stale): raise NotImplementedError
    def _confirm(self, prompt): raise NotImplementedError             # -> bool
    def _show_refused(self, reason): raise NotImplementedError        # non-modal status line
    def _apply_theme(self): raise NotImplementedError

    def _set_notices(self, notices):
        """The entry's standing lines under its head, [(severity, line)],
        worst first (`entry_notices`): the link (V1). A toolkit draws them;
        the base only computes them."""

    def _gate_words(self, element):
        """Why a down link greys `element`, or "" (the toolkit's own gate
        reason comes after this one)."""
        if self.link_down and link_holds(element):
            return self._link_reason
        return ""


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
        """The disc's press. It clears only while EVERY model is latched
        (round 7, L1): with one model stopped from its own switch the disc
        still reads Stop, and a press stops the rest instead of opening the
        clear confirmation over five live models."""
        if stop_words(self.controller.stop_state)["action"] != "clear":
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

    def run_action(self, action):
        """The acknowledgement dialog's action key (rb-restart R1): the same
        path as a press of a button on that panel (`name` is a model, or
        `events.SETUP_PANEL`), so a refusal shows on that panel and a
        confirmation is asked as usual. `action` is `Event.action` (or its
        dict). Returns the Result, or None when there is nothing to run."""
        if not action or self._closing:
            return None
        name, command = action.get("name"), action.get("command")
        args = list(action.get("args") or ())
        events.debug("Action", f"{action.get('label')}: {name} {command} {args}",
                     source="View")
        element = {"type": "button", "text": action.get("label") or command,
                   "command": command, "args": args, "inputs": []}
        panel = self._action_panel(name)
        if panel is not None:
            return panel._run(element)
        # No panel of it is drawn: the same calls, the Dashboard's question.
        if name == events.SETUP_PANEL:
            call = self.setup.run
        else:
            def call(c, i=None, a=()):
                return self.controller.run(name, c, i, a)
        result = call(command, {}, tuple(args))
        if result.needs_confirm and self._confirm(result.reason):
            result = call(result.command, result.inputs, (*result.args, True))
        if result.is_refused:
            events.warn("Refused", f"{action.get('label')}: {result.reason}",
                        source=str(name))
        return result

    def _action_panel(self, name):
        """The drawn PanelView for `name` (`events.SETUP_PANEL` is Setup), or
        None. A toolkit overrides this."""
        return None

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
