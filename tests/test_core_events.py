"""`events` — the one log, and the popup policy that lives only here.

Ports the intent of `tests/edge_cases/test_edge_mvc_error_router.py` (a modal
only for an acknowledged error, a quiet event raises none, a thread exception
reaches the bus) and the `EventBus` half of `tests/core/test_system_manager.py`.
Adds the diagnostics channel from addendum 1: `debug()` is file-only, rate
limited by `every=`, and writes the traceback.
"""
import os
import threading
import time

import pytest

from events import Event, EventLog

from test_core_fakes import FakeClock

pytestmark = pytest.mark.errors


def _log(clock=None):
    return EventLog(clock=clock or time.time)


# -- severity and the popup policy -----------------------------------------

def test_only_error_asks_for_an_acknowledged_popup():
    log = _log()
    assert log.error("Fault", "coil open").needs_ack is True
    assert log.warn("Slow", "poll late").needs_ack is False
    assert log.info("Connected", "COM3").needs_ack is False


def test_an_error_can_opt_out_of_the_popup_for_a_close_path():
    """`Controller._close_models` uses this: a modal raised from inside the
    exit path blocks the exit."""
    assert _log().error("Stop Not Confirmed", "x", ack=False).needs_ack is False


def test_severities_are_the_three_names_every_view_maps():
    log = _log()
    assert log.error("a", "b").severity == "error"
    assert log.warn("a", "b").severity == "warning"
    assert log.info("a", "b").severity == "info"


def test_an_exception_rides_along_with_the_event():
    exc = ValueError("bad")
    assert _log().error("Fault", "bad", exception=exc).exception is exc


# -- dedupe ----------------------------------------------------------------

def test_a_repeat_inside_the_window_collapses_into_one_entry_with_a_count():
    clock = FakeClock()
    log = EventLog(clock=clock)
    first = log.error("Fault", "coil open", source="Probe")
    for _ in range(59):
        clock.advance(0.01)
        log.error("Fault", "coil open", source="Probe")
    assert len(log.since(0)) == 1, "a 60 Hz fault must be one line, not a flood"
    assert first.count == 60


def test_a_repeat_updates_last_seen_but_not_first_seen():
    clock = FakeClock()
    log = EventLog(clock=clock)
    event = log.warn("Slow", "poll late")
    clock.advance(1.0)
    log.warn("Slow", "poll late")
    assert event.first_seen == 1000.0 and event.last_seen == 1001.0


def test_a_repeat_does_not_re_notify_subscribers():
    clock = FakeClock()
    log = EventLog(clock=clock)
    seen = []
    log.subscribe(seen.append)
    log.error("Fault", "coil open")
    clock.advance(0.5)
    log.error("Fault", "coil open")
    assert len(seen) == 1, "a repeat must not raise a second popup"


def test_the_same_text_from_a_different_source_is_a_different_event():
    log = _log()
    log.error("Fault", "coil open", source="Stepper")
    log.error("Fault", "coil open", source="DC")
    assert len(log.since(0)) == 2


def test_the_same_title_with_a_different_message_is_a_different_event():
    log = _log()
    log.warn("Slow", "poll late by 10 ms")
    log.warn("Slow", "poll late by 90 ms")
    assert len(log.since(0)) == 2


def test_a_repeat_after_the_window_is_a_new_event_and_notifies_again():
    clock = FakeClock()
    log = EventLog(clock=clock)
    seen = []
    log.subscribe(seen.append)
    log.error("Fault", "coil open")
    clock.advance(EventLog.DEDUPE_SECONDS + 0.1)
    log.error("Fault", "coil open")
    assert len(log.since(0)) == 2 and len(seen) == 2


def test_the_count_shows_in_the_one_wording_all_three_views_render():
    clock = FakeClock()
    log = EventLog(clock=clock)
    event = log.error("Fault", "coil open", source="Probe")
    assert event.text == "[Probe] Fault: coil open"
    clock.advance(0.1)
    log.error("Fault", "coil open", source="Probe")
    assert event.text == "[Probe] Fault: coil open (x2)"


def test_an_event_with_no_source_drops_the_bracket():
    assert _log().info("Ready", "go").text == "Ready: go"


def test_to_dict_is_what_the_web_client_receives():
    event = _log().error("Fault", "coil open", source="Probe")
    payload = event.to_dict()
    assert payload["severity"] == "error" and payload["needs_ack"] is True
    assert payload["text"] == "[Probe] Fault: coil open"
    assert set(payload) >= {"id", "severity", "source", "title", "message",
                            "needs_ack", "count", "text", "last_seen"}


# -- since(), ids, capacity -------------------------------------------------

def test_since_is_non_destructive_so_every_reader_sees_every_event():
    """The Web client polls `since(latest)`; a desktop view subscribes. One
    must not consume the other's events."""
    log = _log()
    log.info("A", "1")
    log.info("B", "2")
    assert [e.title for e in log.since(0)] == ["A", "B"]
    assert [e.title for e in log.since(0)] == ["A", "B"]
    assert [e.title for e in log.since(1)] == ["B"]
    assert log.since(99) == []


def test_ids_increase_and_latest_id_names_the_newest():
    log = _log()
    first = log.info("A", "1")
    second = log.info("B", "2")
    assert second.id > first.id
    assert log.latest_id == second.id


def test_latest_id_of_an_empty_log_is_zero_so_since_zero_is_everything():
    assert _log().latest_id == 0


def test_the_log_is_capped_and_drops_the_oldest():
    log = EventLog(max_events=5)
    for n in range(20):
        log.info("Tick", str(n))
    kept = log.since(0)
    assert len(kept) == 5
    assert [e.message for e in kept] == ["15", "16", "17", "18", "19"]


def test_clear_empties_the_log_and_the_dedupe_window():
    log = _log()
    log.error("Fault", "coil open")
    log.clear()
    log.error("Fault", "coil open")
    assert len(log.since(0)) == 1, (
        "after a clear, the same fault has to be able to notify again")


# -- subscribers ------------------------------------------------------------

def test_subscribe_is_idempotent_so_a_view_cannot_double_register():
    log = _log()
    seen = []
    log.subscribe(seen.append)
    log.subscribe(seen.append)
    log.info("A", "1")
    assert len(seen) == 1


def test_unsubscribe_stops_delivery_and_is_safe_when_not_subscribed():
    log = _log()
    seen = []
    log.unsubscribe(seen.append)
    log.subscribe(seen.append)
    log.unsubscribe(seen.append)
    log.info("A", "1")
    assert seen == []


def test_a_broken_subscriber_does_not_break_the_model_or_its_peers():
    """A view that raises must not take the publishing model down with it."""
    log = _log()
    seen = []

    def angry(event):
        raise RuntimeError("view exploded")

    log.subscribe(angry)
    log.subscribe(seen.append)
    log.error("Fault", "coil open")
    assert len(seen) == 1


def test_publishing_from_a_worker_thread_reaches_subscribers():
    log = _log()
    seen = []
    log.subscribe(seen.append)
    thread = threading.Thread(target=lambda: log.warn("Slow", "from a worker"))
    thread.start()
    thread.join(2)
    assert [e.message for e in seen] == ["from a worker"]


def test_a_subscriber_added_during_a_publish_does_not_deadlock():
    """`_publish` copies the subscriber list before releasing the lock."""
    log = _log()
    extra = []

    def adder(event):
        log.subscribe(extra.append)

    log.subscribe(adder)
    log.info("A", "1")
    log.info("B", "2")
    assert [e.title for e in extra] == ["B"]


# -- the debug channel: file only -------------------------------------------

def test_debug_never_reaches_a_subscriber(tmp_path):
    log = _log()
    seen = []
    log.subscribe(seen.append)
    log.open_file(str(tmp_path))
    try:
        log.debug("Mode", "idle -> auto", source="Probe")
    finally:
        log.close_file()
    assert seen == [], "debug is a diagnostic channel, never a view event"


def test_debug_never_becomes_a_log_entry():
    log = _log()
    log.debug("Mode", "idle -> auto")
    assert log.since(0) == [] and log.latest_id == 0


def test_debug_before_open_file_is_a_no_op_not_a_crash():
    _log().debug("Mode", "idle -> auto")       # must not raise


def test_debug_writes_to_the_file_with_severity_source_and_thread(tmp_path):
    log = _log()
    path = log.open_file(str(tmp_path))
    try:
        log.debug("Mode", "idle -> auto", source="Probe")
    finally:
        log.close_file()
    text = open(path).read()
    assert "DEBUG" in text and "[Probe] Mode: idle -> auto" in text
    assert threading.current_thread().name in text


def test_every_severity_is_also_written_to_the_file(tmp_path):
    log = _log()
    path = log.open_file(str(tmp_path))
    try:
        log.info("Connected", "COM3")
        log.warn("Slow", "poll late")
        log.error("Fault", "coil open")
    finally:
        log.close_file()
    text = open(path).read()
    for word in ("INFO", "WARNING", "ERROR", "Connected", "Slow", "Fault"):
        assert word in text


def test_the_file_carries_the_full_traceback_of_a_caught_exception(tmp_path):
    log = _log()
    path = log.open_file(str(tmp_path))
    try:
        try:
            raise ValueError("the board said no")
        except ValueError as exc:
            log.debug("Write", "frame rejected", source="Probe", exception=exc)
    finally:
        log.close_file()
    text = open(path).read()
    assert "Traceback (most recent call last)" in text
    assert "ValueError: the board said no" in text
    assert "test_core_events.py" in text


def test_open_file_names_the_path_and_announces_itself(tmp_path):
    log = _log()
    path = log.open_file(str(tmp_path))
    log.close_file()
    assert os.path.isfile(path) and path.startswith(str(tmp_path))
    assert "log opened" in open(path).read()


def test_close_file_is_idempotent_and_later_writes_are_dropped(tmp_path):
    log = _log()
    path = log.open_file(str(tmp_path))
    log.close_file()
    log.close_file()
    log.info("After", "close")
    assert "After" not in open(path).read()


# -- every=: a loop reports without flooding --------------------------------

def test_every_rate_limits_one_call_site(tmp_path):
    clock = FakeClock()
    log = EventLog(clock=clock)
    path = log.open_file(str(tmp_path))
    try:
        for _ in range(50):
            log.debug("Poll", "position read", source="Probe", every=1.0)
            clock.advance(0.01)
    finally:
        log.close_file()
    lines = [ln for ln in open(path).read().splitlines() if "Poll" in ln]
    assert len(lines) == 1, f"50 polls in 0.5 s produced {len(lines)} lines"


def test_the_line_that_does_get_through_says_how_many_were_suppressed(tmp_path):
    clock = FakeClock()
    log = EventLog(clock=clock)
    path = log.open_file(str(tmp_path))
    try:
        log.debug("Poll", "position read", source="Probe", every=1.0)
        for _ in range(9):
            clock.advance(0.01)
            log.debug("Poll", "position read", source="Probe", every=1.0)
        clock.advance(2.0)
        log.debug("Poll", "position read", source="Probe", every=1.0)
    finally:
        log.close_file()
    text = open(path).read()
    assert "(+9 suppressed)" in text


def test_every_is_per_source_and_title_so_two_loops_do_not_silence_each_other(tmp_path):
    clock = FakeClock()
    log = EventLog(clock=clock)
    path = log.open_file(str(tmp_path))
    try:
        log.debug("Poll", "x", source="Stepper", every=1.0)
        log.debug("Poll", "x", source="DC", every=1.0)
        log.debug("Jog", "x", source="Stepper", every=1.0)
    finally:
        log.close_file()
    lines = [ln for ln in open(path).read().splitlines()
             if "Poll:" in ln or "Jog:" in ln]
    assert len(lines) == 3


def test_without_every_a_debug_line_is_never_suppressed(tmp_path):
    log = _log()
    path = log.open_file(str(tmp_path))
    try:
        for _ in range(5):
            log.debug("Handshake", "identity S", source="Probe")
    finally:
        log.close_file()
    assert open(path).read().count("Handshake") == 5


# -- thread / excepthook ----------------------------------------------------

def test_hook_exceptions_is_idempotent():
    log = _log()
    import sys
    saved_sys, saved_thread = sys.excepthook, threading.excepthook
    try:
        log.hook_exceptions()
        first = sys.excepthook
        log.hook_exceptions()
        assert sys.excepthook is first
    finally:
        sys.excepthook, threading.excepthook = saved_sys, saved_thread


def test_an_uncaught_exception_on_any_thread_reaches_the_log():
    """Ported from `test_edge_mvc_error_router.test_a_thread_exception_reaches_the_bus`."""
    log = _log()
    import sys
    saved_sys, saved_thread = sys.excepthook, threading.excepthook
    seen = []
    log.subscribe(seen.append)
    try:
        log.hook_exceptions()

        def explode():
            raise RuntimeError("worker died")

        thread = threading.Thread(target=explode, name="poller")
        thread.start()
        thread.join(2)
    finally:
        sys.excepthook, threading.excepthook = saved_sys, saved_thread
    assert [e.title for e in seen] == ["Thread Crashed"]
    assert "poller" in seen[0].message and "worker died" in seen[0].message
    assert seen[0].needs_ack is True


def test_the_sys_hook_reports_but_leaves_keyboard_interrupt_alone():
    log = _log()
    import sys
    saved_sys, saved_thread = sys.excepthook, threading.excepthook
    seen = []
    log.subscribe(seen.append)
    try:
        log.hook_exceptions()
        hook = sys.excepthook
        try:
            raise RuntimeError("main died")
        except RuntimeError as exc:
            hook(RuntimeError, exc, exc.__traceback__)
    finally:
        sys.excepthook, threading.excepthook = saved_sys, saved_thread
    assert [e.title for e in seen] == ["Unhandled Exception"]
    assert "RuntimeError: main died" in seen[0].message


def test_a_thread_exit_is_not_reported_as_a_crash():
    log = _log()
    import sys
    saved_sys, saved_thread = sys.excepthook, threading.excepthook
    seen = []
    log.subscribe(seen.append)
    try:
        log.hook_exceptions()
        args = type("Args", (), {"exc_type": SystemExit, "exc_value": SystemExit(),
                                 "exc_traceback": None, "thread": None})()
        threading.excepthook(args)
    finally:
        sys.excepthook, threading.excepthook = saved_sys, saved_thread
    assert seen == []


# -- Event itself -----------------------------------------------------------

def test_an_event_has_slots_so_a_60_hz_fault_stays_cheap():
    assert not hasattr(Event(1, "info", "s", "t", "m", None, False, 0.0), "__dict__")


def test_the_dedupe_key_is_severity_source_title_message():
    event = Event(1, "error", "Probe", "Fault", "coil open", None, True, 0.0)
    assert event.key == ("error", "Probe", "Fault", "coil open")


def test_forget_ends_the_dedupe_episode_so_the_next_one_is_new():
    """Round 7 (Web CCR 2): stop, clear, stop again inside the dedupe window
    produced ONE "Stop Not Confirmed" entry with a count and no new id, so
    the second unconfirmed stop never reached the page's event feed. A
    clear calls `forget`; the next one is a new event. A 60 Hz fault still
    folds (the flood rule stands)."""
    clock = FakeClock()
    log = EventLog(clock=clock)
    seen = []
    log.subscribe(seen.append)
    first = log.error("Stop Not Confirmed", "Rotator did not confirm the stop.", source="Controller")
    clock.advance(1.0)
    log.forget("Stop Not Confirmed")
    second = log.error("Stop Not Confirmed", "Rotator did not confirm the stop.", source="Controller")
    assert second.id != first.id and second.count == 1 and first.count == 1
    assert [e.id for e in seen] == [first.id, second.id]
    a = log.error("Fault", "coil open", source="Probe")
    clock.advance(0.5)
    b = log.error("Fault", "coil open", source="Probe")
    assert a is b and a.count == 2


# -- attention: a warning that asks for an acknowledgement (rb-ack, A1/A2) ----

def test_a_warning_can_ask_for_an_acknowledgement():
    """A1: `warn` takes the `ack=` keyword `error` has; the default is still
    a tray line."""
    log = _log()
    assert log.warn("Idle Timeout", "powered down", ack=True).needs_ack is True
    assert log.warn("Idle Timeout Soon", "soon").needs_ack is False
    assert log.warn("Slow", "poll late", ack=False).needs_ack is False


def test_an_acknowledged_warning_is_still_a_warning():
    """Only the popup changes: severity, dedupe and the dict are as before."""
    clock = FakeClock()
    log = EventLog(clock=clock)
    first = log.warn("Rotator Unreachable", "x", source="Rotator", ack=True)
    clock.advance(0.5)
    again = log.warn("Rotator Unreachable", "x", source="Rotator", ack=True)
    assert first is again and first.count == 2
    assert first.severity == "warning"
    assert first.to_dict()["needs_ack"] is True


def test_the_attention_set_is_exactly_the_titles_the_lead_named():
    """A2: one place marks a warning as asking for attention."""
    import events as events_module
    # rb-restart R2 (owner 2026-09-28, "should prompt for restart"): the two
    # update prompts ask too; each carries its action.
    # Owner 2026-09-28 (the launcher's flash, unattended): the startup
    # firmware check asks once, and Flash now is its action.
    # Owner 2026-09-30 (L4, "track dropped packets like events and display
    # warnings"): a lost serial link asks too.
    assert events_module.ATTENTION == frozenset({
        "Idle Timeout", "Temperature Disconnected", "Rotator Unreachable",
        "Heater Off Not Sent", "Update Ready", "Restart Needed",
        "Firmware Out of Date", "Connection Lost", "Board Reset Suspected"})
    assert isinstance(events_module.ATTENTION, frozenset)
    # The countdown, the silent browser and the map's soft notices stay
    # tray lines.
    for title in ("Idle Timeout Soon", "Browser Silent", "No Picture",
                  "Empty Trial"):
        assert title not in events_module.ATTENTION
    assert events_module.IDLE_TIMEOUT in events_module.ATTENTION
    assert events_module.IDLE_TIMEOUT_SOON not in events_module.ATTENTION


def test_every_attention_title_is_raised_with_ack_and_nothing_else_is():
    """The sites and the set agree: a warning title raised with `ack=True`
    anywhere under src/ is in ATTENTION, and each ATTENTION title is raised
    with `ack=True` at its site. Read from the source because some sites
    (the heater's unsendable off frame) are awkward to reach."""
    import ast
    import pathlib
    import events as events_module
    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    names = {k: v for k, v in vars(events_module).items()
             if isinstance(v, str) and k.isupper()}
    acked, plain = set(), set()
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "warn" and node.args):
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                title = first.value
            elif isinstance(first, ast.Attribute) and first.attr in names:
                title = names[first.attr]
            else:
                continue
            ack = [k for k in node.keywords if k.arg == "ack"]
            if ack and isinstance(ack[0].value, ast.Constant) and ack[0].value.value:
                acked.add(title)
            else:
                plain.add(title)
    assert acked == events_module.ATTENTION, (acked, events_module.ATTENTION)
    assert not (plain & events_module.ATTENTION), plain & events_module.ATTENTION


# -- rb-restart R1: an action on an acknowledged notice ------------------------

def test_an_acknowledged_notice_carries_its_action_into_the_dict():
    log = _log()
    event = log.warn("Restart Needed", "Updated to def5678.", ack=True,
                     action=("Restart now", "__setup__", "restart_station", (True,)))
    assert event.action == {"label": "Restart now", "name": "__setup__",
                            "command": "restart_station", "args": [True]}
    assert event.to_dict()["action"] == event.action
    # Three items are the brief's shape; the args default to none.
    plain = log.error("Update Failed", "x", action=("Retry", "__setup__", "apply_update"))
    assert plain.to_dict()["action"] == {"label": "Retry", "name": "__setup__",
                                         "command": "apply_update", "args": []}


def test_a_notice_without_an_action_says_none():
    log = _log()
    assert log.warn("Idle Timeout", "x", ack=True).to_dict()["action"] is None
    assert log.error("Fault", "x").to_dict()["action"] is None
    assert log.info("Connected", "COM3").to_dict()["action"] is None


def test_an_action_only_rides_on_a_notice_that_asks_for_acknowledgement():
    """An action is a key on the acknowledgement dialog: a tray line has no
    dialog to put it on, so asking for one is a programming error."""
    log = _log()
    with pytest.raises(ValueError):
        log.warn("Slow", "poll late", action=("Go", "__setup__", "x"))
    with pytest.raises(ValueError):
        log.error("Fault", "x", ack=False, action=("Go", "__setup__", "x"))
    with pytest.raises(ValueError):
        log.warn("Update Ready", "x", ack=True, action=("Go", "__setup__"))


def test_the_action_survives_the_trip_to_json():
    import json
    event = _log().warn("Update Ready", "2 new commits are ready.", ack=True,
                        action=("Update now", "__setup__", "apply_update"))
    back = json.loads(json.dumps(event.to_dict()))
    assert back["action"] == {"label": "Update now", "name": "__setup__",
                              "command": "apply_update", "args": []}


def test_the_setup_panel_target_is_named_once():
    import events as events_module
    from events import events
    assert events_module.SETUP_PANEL == "__setup__"
    assert events.SETUP_PANEL == "__setup__"
    assert events.UPDATE_READY == "Update Ready"
    assert events.RESTART_NEEDED == "Restart Needed"


def test_flush_file_writes_what_is_buffered_and_keeps_the_file_open(tmp_path):
    log = _log()
    path = log.open_file(str(tmp_path))
    log.info("Restart", "closing every model")
    log.flush_file()
    assert "closing every model" in open(path, encoding="utf-8").read()
    log.info("After", "still open")
    log.close_file()
    assert "still open" in open(path, encoding="utf-8").read()
    log.flush_file()        # closed: nothing to flush, never an error
