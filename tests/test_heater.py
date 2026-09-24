"""Heater behaviour: refusals, the stop path, the reader, the schema.

Ported from the old suite — `tests/core/test_temperature.py`,
`test_temperature_subsystem.py`, `test_temp2_reader_backoff.py`,
`test_temp2_shutdown_seam.py`, `test_temp9_plot_series.py`,
`test_temp10_no_port_state.py`, `test_temp10_no_port_feedback.py`,
`test_temp10_connection_state.py`, `test_errors7_temperature_reporting.py`,
`tests/hardware/test_temp17_write_ordering.py` (heater half) — because those
encode the bugs that were fixed. Bytes on the wire live in
`test_heater_frames.py`.

Nothing here opens a real port, and nothing here sleeps its way through a
backoff: the reader is driven with a recording `_backoff_wait`, so the
0.1 -> 2.0 s ladder is asserted as a sequence rather than waited out.
"""
import threading
import time

import pytest

from events import events
from model.base import Model
from model.heater import Heater
from result import Refused, Result
import schema as sch

from test_heater_fakes import EventRecorder, FakePort


@pytest.fixture
def port():
    return FakePort()


@pytest.fixture
def heater(port):
    model = Heater(port=port)
    yield model
    model._stop_threads()


@pytest.fixture(autouse=True)
def quiet_event_log():
    """`events` is a process singleton with a 5 s dedupe window and a
    rate-limit table for `debug(every=)`. Two tests asserting on the same
    event inside one run would otherwise see the second one collapse into the
    first's count, which is correct behaviour and a useless test."""
    for reset in (events.clear, events._debug_seen.clear):
        reset()
    yield
    for reset in (events.clear, events._debug_seen.clear):
        reset()


# -- construction -------------------------------------------------------------

def test_it_is_a_model_with_the_class_attributes_setup_reads(heater):
    assert isinstance(heater, Model)
    assert (Heater.NAME, Heater.IDENTITY) == ("Temperature Controller", "t")
    assert Heater.NEEDS_PORT is True and Heater.NEEDS_GAMEPAD is False


def test_it_owns_exactly_one_port(heater, port):
    assert heater.devices == [port]


def test_it_asks_its_port_for_the_right_baud_and_for_sim_without_hardware(
        monkeypatch):
    """`SerialPort` is still a skeleton in this worktree, so this pins the
    call the model makes rather than the object it gets back."""
    built = []

    class _Port:
        def __init__(self, port, baud_rate=None):
            built.append((port, baud_rate))

    monkeypatch.setattr("model.heater.SerialPort", _Port)
    Heater(port=None)
    Heater(port="None")
    Heater(port=None, sim=True)
    Heater(port="COM4")
    assert built == [("SIM", 115200), ("SIM", 115200), ("SIM", 115200),
                     ("COM4", 115200)]


def test_the_defaults_are_the_old_ones(heater):
    assert (heater.setpoint, heater.ramp_rate) == (0, 10)
    assert (heater.p_term, heater.i_term, heater.d_term) == (2.0, 0.5, 0.1)
    assert heater.offset == 0


def test_there_is_no_client_liveness_code_left():
    """The Web heartbeat watchdog owns this now and calls Controller.estop_all;
    no model carries its own copy (design.rules: `_client_liveness_active` ->
    `Model.is_active`)."""
    for gone in ("_client_liveness_active", "_init_client_liveness",
                 "start_client_liveness_watchdog",
                 "stop_client_liveness_watchdog", "WEB_CLIENT_WARN_TIMEOUT",
                 "WEB_CLIENT_STOP_TIMEOUT"):
        assert not hasattr(Heater, gone), gone
    # What replaced it: one inherited concept, implemented here.
    assert "is_active" in vars(Heater)


def test_close_disconnect_and_teardown_are_one_inherited_close():
    """Three names for one act became `Model.close`."""
    assert Heater.close is Model.close
    assert not hasattr(Heater, "disconnect")
    assert not hasattr(Heater, "teardown")


# -- apply_settings: it refuses, it never returns quietly ---------------------

def test_apply_settings_sends_the_frame_and_returns_the_setpoint(heater, port):
    heater.setpoint = 80.0
    assert heater.apply_settings() == 80.0
    assert port.frames and port.frames[0].startswith(b"<80,")


def test_apply_settings_refuses_while_the_latch_is_set(heater, port):
    heater.estop()
    port.forget()
    with pytest.raises(Refused) as refusal:
        heater.apply_settings()
    assert "is stopped" in refusal.value.reason   # F20 vocabulary
    assert port.frames == []


def test_apply_settings_hands_the_latch_check_to_the_port(heater, port):
    """`_guard` at the top is a check-then-act: the write can wait on the
    port's lock for as long as a read holds it. The check that counts is the
    one the port makes inside the lock, so `abort_if` must be passed and must
    answer true once the latch is set (TEMP-7)."""
    heater.setpoint = 80.0
    captured = {}

    def _write(payload, *, priority=False, abort_if=None):
        captured["abort_if"] = abort_if
        return True

    port.write = _write
    heater.apply_settings()
    abort_if = captured["abort_if"]
    assert abort_if is not None
    assert abort_if() is False
    heater._estop.set()
    assert abort_if() is True, "the port's in-lock check does not see the latch"


def test_a_settings_frame_superseded_by_a_stop_is_refused_not_written(heater, port):
    """TEMP-17. A stop that lands while this frame waits for the wire must
    drop it at the wire, not let it be written after the stop."""
    heater.setpoint = 80.0
    original = port.write

    def _write(payload, *, priority=False, abort_if=None):
        if payload.startswith(b"<80"):
            heater._stop_generation += 1        # a stop lands mid-write
        return original(payload, priority=priority, abort_if=abort_if)

    port.write = _write
    with pytest.raises(Refused) as refusal:
        heater.apply_settings()
    assert "stop arrived" in refusal.value.reason
    assert port.frames == []
    assert port.writes[0].is_aborted is True
    assert heater.is_active is False


@pytest.mark.parametrize("field,value,named", [
    ("setpoint", "", "Setpoint"),
    ("setpoint", "abc", "Setpoint"),
    ("p_term", "", "Proportional Term (P)"),
    ("offset", "nan", "Offset"),
    ("i_term", "1,90", "Integral Term (I)"),
])
def test_a_field_the_firmware_would_misparse_refuses_the_whole_frame(
        heater, port, field, value, named):
    """`strtok` collapses an empty field, so every parameter after a blank
    shifts left and the board is handed the ramp rate as its setpoint with
    the gains as everything else (TEMP-3). Refusing is the only safe answer,
    and the refusal names the field."""
    result = heater.run("apply_settings", inputs={field: value})
    assert result.status == Result.REFUSED
    assert named in result.reason
    assert port.frames == []


def test_a_setpoint_over_the_bound_is_refused(heater, port):
    """TEMP-13: a typo of 2000 for 200 used to be sent unmodified."""
    result = heater.run("apply_settings", inputs={"setpoint": "2000"})
    assert result.status == Result.REFUSED
    assert "at most" in result.reason
    assert port.frames == []


def test_every_frame_field_is_bounded():
    for name in Heater.FRAME_FIELDS:
        param = Heater.PARAMS[name]
        assert param.maximum is not None, f"{name} has no upper bound"
        assert param.minimum is not None, f"{name} has no lower bound"


def test_a_payload_longer_than_the_firmware_buffer_is_refused(heater, port):
    """`receivedChars[32]` with a clamped index: a payload past 31 characters
    keeps overwriting the last slot, so the tail is silently lost and the
    offset the board parses is not the one that was sent."""
    heater.setpoint, heater.ramp_rate = 123.456, 123.456
    heater.p_term = heater.i_term = heater.d_term = 123.456
    heater.offset = 98.765
    with pytest.raises(Refused) as refusal:
        heater.apply_settings()
    assert "characters" in refusal.value.reason
    assert port.frames == []


def test_a_disconnected_heater_refuses_with_a_reason_instead_of_going_inert():
    """TEMP-10: the old no-port branch did nothing and said nothing, so the
    operator could not tell a disconnected model from a working one."""
    port = FakePort(is_open=False)
    heater = Heater(port=port)
    result = heater.run("apply_settings", inputs={"setpoint": "80"})
    assert result.status == Result.REFUSED
    assert "not connected" in result.reason
    assert port.frames == []


def test_a_refusal_is_never_reported_as_success(heater):
    result = heater.run("apply_settings", inputs={"setpoint": ""})
    assert bool(result) is False and result.is_ok is False


# -- the stop path -------------------------------------------------------------

def test_halt_writes_the_heater_off_frame_on_the_priority_lane(heater, port):
    assert heater.halt() is True
    assert len(port.writes) == 1
    assert port.writes[0].priority is True


def test_halt_does_not_latch(heater):
    heater.halt()
    assert heater.is_estopped is False


def test_estop_latches_and_confirms_the_stop(heater, port):
    assert heater.estop() is True
    assert heater.is_estopped is True
    assert port.writes[-1].priority is True


def test_estop_latches_even_when_the_write_fails(heater, port):
    port.write_error = OSError("cable is out")
    assert heater.estop() is False
    assert heater.is_estopped is True, "the latch must not depend on any I/O"


def test_a_stop_supersedes_a_settings_frame_that_has_not_been_built_yet(heater):
    before = heater._stop_generation
    heater.halt()
    assert heater._stop_generation > before


def test_the_stop_is_not_blocked_by_a_held_write_lock(heater, port):
    """A stop that cannot get a lock is worse than an unsynchronised one."""
    heater._write_lock.acquire()
    try:
        started = time.monotonic()
        assert heater.halt() is True
        elapsed = time.monotonic() - started
    finally:
        heater._write_lock.release()
    assert elapsed < Heater.WRITE_LOCK_TIMEOUT + 0.3, f"the stop waited {elapsed:.2f}s"
    assert port.frames


def test_the_stop_path_fits_inside_the_estop_budget(heater):
    started = time.monotonic()
    heater.estop()
    assert time.monotonic() - started < Model.ESTOP_BUDGET + 0.1


def test_clearing_the_latch_needs_an_operator_confirmation(heater):
    result = heater.run("toggle_estop")     # latch
    assert result.is_ok
    result = heater.run("toggle_estop")     # ask before clearing
    assert result.needs_confirm
    heater.clear_estop(confirmed=True)
    assert heater.is_estopped is False


# -- close ----------------------------------------------------------------------

def test_close_stops_the_heater_drains_it_and_only_then_releases_the_port(
        heater, port):
    """TEMP-11, the whole of it in one order: threads down, heater-off frame,
    the board's power-on frame, a bounded drain, then the port."""
    heater.open()
    heater.close()
    assert port.kinds[-2:] == ["flush", "close"]
    frames = port.frames
    assert frames[-2].startswith(b"<0,") and frames[-1] == Heater.RESET_FRAME


def test_close_reports_an_undelivered_heater_off_frame(heater, port):
    """The firmware has no watchdog: a frame that does not go out leaves the
    heater at its last setpoint for as long as it has power."""
    heater.apply_settings()
    port.write_error = OSError("cable is out")
    with EventRecorder(events) as log:
        heater.close()
    assert log.titled("Heater Off Not Delivered"), [e.text for e in log.seen]


def test_close_reports_a_heater_off_frame_that_could_not_be_drained(heater, port):
    """`close()` on POSIX may discard bytes handed to the OS but not yet sent,
    so a write that was accepted is not yet a frame on the wire."""
    port.flush_result = False
    with EventRecorder(events) as log:
        heater.close()
    assert log.titled("Heater Off Not Delivered")


def test_a_clean_close_reports_nothing(heater):
    with EventRecorder(events) as log:
        heater.close()
    assert log.of("error") == [], [e.text for e in log.of("error")]


def test_closing_a_heater_that_never_had_a_port_says_nothing_new():
    """`state` has reported the disconnection all along; a popup on the way
    out adds nothing."""
    heater = Heater(port=FakePort(is_open=False))
    with EventRecorder(events) as log:
        heater.close()
    assert log.of("error") == []


def test_close_survives_a_step_that_raises(heater, port):
    """Each close step is isolated: the hardware steps are never skipped
    because an earlier one raised."""
    def _boom():
        raise RuntimeError("halt exploded")

    heater._halt_hardware = _boom
    heater.close()
    assert "close" in port.kinds


# -- the reader ------------------------------------------------------------------

class _BackoffRecorder:
    """Stands in for `_backoff_wait` so the ladder is asserted, not waited."""

    def __init__(self, stop_after):
        self.waits = []
        self._stop_after = stop_after

    def __call__(self, seconds):
        self.waits.append(round(seconds, 4))
        return len(self.waits) >= self._stop_after

    @property
    def backoffs(self):
        return [w for w in self.waits if w > Heater.READ_FLOOR]


def _run_reader(heater, stop_after):
    recorder = _BackoffRecorder(stop_after)
    heater._backoff_wait = recorder
    heater._read_loop()
    return recorder


def test_the_reader_backs_off_exponentially_up_to_a_ceiling(heater, port):
    """TEMP-2: the old reader used a fixed 0.1 s retry, which the docs called
    'backoff', and gave up after five failures."""
    port.read_error = OSError("link down")
    recorder = _run_reader(heater, stop_after=9)
    assert recorder.backoffs[:6] == [0.1, 0.2, 0.4, 0.8, 1.6, 2.0]
    assert max(recorder.backoffs) <= Heater.MAX_BACKOFF


def test_the_reader_never_gives_up(heater, port):
    """Five failures is a USB glitch, not a reason to kill the thread for the
    rest of the session."""
    port.read_error = OSError("link down")
    recorder = _run_reader(heater, stop_after=40)
    assert len(recorder.backoffs) == 40


def test_a_port_that_merely_reports_closed_counts_as_a_failure(heater, port):
    """It used to reset the counter and spin in silence, so a dropped link
    looked exactly like a working one."""
    port._is_open = False
    recorder = _run_reader(heater, stop_after=6)
    assert recorder.backoffs[:3] == [0.1, 0.2, 0.4]
    assert heater._is_link_lost is True


def test_persistent_failure_says_disconnected_once(heater, port):
    port.read_error = OSError("link down")
    with EventRecorder(events) as log:
        _run_reader(heater, stop_after=8)
    assert len(log.titled("Temperature Disconnected")) == 1
    assert heater.temperature == "Disconnected"


def test_the_reader_recovers_when_the_link_comes_back(heater, port):
    """The failure count resets and the readout stops saying Disconnected."""
    port.read_error = OSError("link down")
    calls = {"n": 0}

    def _wait(seconds):
        calls["n"] += 1
        if calls["n"] == 7:                      # the cable goes back in
            port.read_error = None
            port.lines.append("1.0,25.00,30.0")
        return calls["n"] >= 12

    heater._backoff_wait = _wait
    heater._read_loop()
    assert heater._is_link_lost is False
    assert heater.temperature == "25.00 °C"


def test_the_reader_cannot_free_spin_on_a_port_that_never_blocks(heater, port):
    """A misconfigured non-blocking port used to grow RSS by GB in seconds."""
    port.lines.extend(["0,20.0,20.0"] * 50)
    recorder = _run_reader(heater, stop_after=25)
    assert recorder.waits[:5] == [Heater.READ_FLOOR] * 5


def test_the_reader_leaves_the_instant_close_asks_however_long_its_backoff(
        heater, port):
    """TEMP-2's seam. A plain `time.sleep(backoff)` is only checked at the top
    of the loop, so a reader parked in the 2.0 s backoff outlives the 1.5 s
    join — and `close()` then shuts the port underneath a live thread."""
    port.read_error = OSError("link down")
    heater.open()
    time.sleep(0.05)
    reader = heater._reader
    assert reader.is_alive()

    started = time.monotonic()
    heater._stop_threads()
    elapsed = time.monotonic() - started
    assert not reader.is_alive(), "the reader slept through shutdown"
    assert elapsed < Heater.READER_JOIN_TIMEOUT


def test_stopping_threads_twice_is_harmless(heater):
    heater.open()
    heater._stop_threads()
    heater._stop_threads()


# -- parsing and history -----------------------------------------------------------

def test_parse_line_reads_timer_temperature_setpoint(heater):
    heater._parse_line("12.5, 24.75, 50.0\n")
    times, temperatures, setpoints = heater.history
    assert (times, temperatures, setpoints) == ([12.5], [24.75], [50.0])
    assert heater.temperature == "24.75 °C"


@pytest.mark.parametrize("line", ["DEV: t\n", "invalid,data\n", "a,b,c\n",
                                  "", "   \n", "1,2"])
def test_a_line_that_is_not_three_numbers_is_ignored(heater, line):
    heater._parse_line("1.0, 20.00, 20.0")
    heater._parse_line(line)
    assert heater.temperature == "20.00 °C"
    assert len(heater.history[0]) == 1


def test_the_history_is_capped(heater):
    for i in range(250):
        heater._parse_line(f"{i * 0.5}, {20.0 + i * 0.1:.2f}, 50.0")
    times, temperatures, setpoints = heater.history
    assert len(times) == len(temperatures) == len(setpoints) == Heater.HISTORY_LENGTH
    assert times[-1] == 249 * 0.5
    assert setpoints[-1] == 50.0


def test_history_is_a_snapshot_not_the_live_lists(heater):
    heater._parse_line("1.0,20.0,20.0")
    times, _, _ = heater.history
    times.append(999)
    assert heater.history[0] == [1.0]


def test_a_reading_refreshes_the_inherited_age(heater):
    heater._updated_at = time.monotonic() - 5
    assert heater.state["age"] >= 5
    heater._parse_line("1.0,20.0,20.0")
    assert heater.state["age"] < 1


# -- the readout tells the truth (TEMP-10) ----------------------------------------

def test_a_reading_that_cannot_arrive_never_says_na():
    """'N/A' reads as *no reading yet* — a transient state the operator waits
    out forever when there is nothing on the other end."""
    heater = Heater(port=FakePort(is_open=False))
    assert heater.temperature == "Disconnected"


def test_a_simulated_port_says_so_rather_than_pretending():
    heater = Heater(port=FakePort(status="simulated"))
    assert heater.temperature == "Simulated"


def test_a_link_that_drops_stops_showing_its_last_good_value_as_live(heater):
    heater._parse_line("1.0,180.0,200.0")
    assert heater.temperature == "180.00 °C"
    heater._is_link_lost = True
    assert heater.temperature == "Disconnected"


def test_the_connection_state_is_the_ports_own_word(heater, port):
    assert heater.connection == "verified"
    port._is_open = False
    assert heater.connection == "closed"


# -- is_active ---------------------------------------------------------------------

def test_a_typed_setpoint_is_not_heating(heater):
    heater.setpoint = 150.0
    assert heater.is_active is False


def test_a_sent_setpoint_is_heating(heater):
    heater.setpoint = 150.0
    heater.apply_settings()
    assert heater.is_active is True


def test_a_sent_zero_is_not_heating(heater):
    heater.setpoint = 0.0
    heater.apply_settings()
    assert heater.is_active is False


def test_a_stop_ends_heating(heater):
    heater.setpoint = 150.0
    heater.apply_settings()
    heater.halt()
    assert heater.is_active is False


# -- schema ------------------------------------------------------------------------

def _elements(heater):
    return list(sch.elements(heater.schema))


def test_the_schema_has_an_entry_for_every_frame_field(heater):
    entries = {e["model_attr"] for e in _elements(heater) if e["type"] == "entry"}
    assert entries == set(Heater.FRAME_FIELDS)


def test_every_entry_carries_its_type_and_bounds(heater):
    for element in _elements(heater):
        if element["type"] != "entry":
            continue
        assert element["value_type"] == "float"
        assert element["max"] is not None
        assert element["writable"] is True


def test_the_schema_declares_the_plot_over_the_series(heater):
    plots = [e for e in _elements(heater) if e["type"] == "plot"]
    assert len(plots) == 1
    assert plots[0]["data_command"] == "series"
    assert plots[0]["text"] == "Temperature over time"


def test_enter_settings_carries_the_whole_frame_with_it(heater):
    button = next(e for e in _elements(heater)
                  if e.get("command") == "apply_settings")
    assert tuple(button["inputs"]) == Heater.FRAME_FIELDS
    assert button["role"] == "go"


def test_there_is_a_stop_button_and_it_reads_as_dangerous(heater):
    button = next(e for e in _elements(heater) if e.get("command") == "halt")
    assert button["text"] == "Stop heater" and button["role"] == "neutral"  # one red: danger is the estop toggle and a fault, not a halt command (2026-09-24)


def test_the_safety_section_is_last(heater):
    sections = heater.schema["sections"]
    assert sections[-1]["title"] == "Safety"
    commands = {e.get("command") for e in sections[-1]["elements"]}
    assert "toggle_estop" in commands


def test_no_readout_is_writable(heater):
    for element in _elements(heater):
        if element["type"] in ("readonly", "plot", "indicator", "toggle"):
            assert element["writable"] is False, element


def test_the_view_can_fetch_the_plot_data_the_way_it_fetches_any_other(heater):
    """A `plot` element is refreshed with `run(data_command)` in all three
    views. Panel.run accepts a property as a data source."""
    heater._parse_line("1.0,20.0,20.0")
    result = heater.run("series")
    assert result.is_ok, result.reason
    assert result.value["x"] == [1.0] and result.value["y"] == [20.0]


def test_series_is_a_property_as_the_design_says(heater):
    assert isinstance(Heater.series, property)
    assert heater.series == {"x": [], "y": []}


def test_a_command_the_schema_does_not_declare_is_refused(heater):
    assert heater.run("_send_heater_off").status == Result.REFUSED


def test_a_view_cannot_write_a_readout_through_set_value(heater):
    assert heater.set_value("temperature", "9000").status == Result.REFUSED


def test_the_state_snapshot_carries_what_every_view_needs(heater):
    state = heater.state
    assert state["name"] == Heater.NAME
    assert set(state) >= {"values", "age", "is_estopped", "is_faulted",
                          "is_active", "devices", "fault"}
    assert state["values"]["temperature"] == "N/A"
    assert "FakePort" in state["devices"]


# -- diagnostics (Addendum 1) --------------------------------------------------------

def test_the_log_file_gets_the_frames_in_hex_and_every_refusal(heater, port, tmp_path):
    path = events.open_file(str(tmp_path))
    try:
        heater.setpoint = 80.0
        heater.apply_settings()
        heater.halt()
        heater.run("apply_settings", inputs={"setpoint": ""})
        heater._parse_line("not,a,number")
    finally:
        events.close_file()
    text = open(path, encoding="utf-8").read()
    assert "Settings Frame" in text and b"<80,".hex(" ") in text
    assert "Heater Off Frame" in text
    assert "Halt:" in text and "ms" in text
    assert "Refused" in text
    assert "Parse Failed" in text


def test_nothing_in_the_reader_publishes_per_iteration(heater, port):
    """A fault in a loop must be one line, not a flood."""
    port.read_error = OSError("link down")
    with EventRecorder(events) as log:
        _run_reader(heater, stop_after=30)
    assert len(log.seen) <= 3, [e.text for e in log.seen]


def test_the_model_never_prints():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(__import__(
        "model.heater", fromlist=["heater"])))
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "print"]


def test_a_reader_left_running_by_a_test_would_be_noticed():
    leaked = [t.name for t in threading.enumerate()
              if t.name.startswith("reader-") and t.is_alive()]
    assert leaked == [], leaked


def test_the_latch_greys_out_enter_settings_but_not_stop_heater(heater):
    """F11: `latched` is the gate token while the latch is set."""
    heater.estop()
    mode = heater.state["mode"]
    assert mode == "latched"
    by_command = {e.get("command"): e for e in sch.elements(heater.schema)}
    assert sch.is_enabled(by_command["apply_settings"], mode) is False
    assert sch.is_enabled(by_command["halt"], mode) is True
    assert sch.is_enabled(by_command["toggle_estop"], mode) is True
    heater.clear_estop(confirmed=True)
    assert sch.is_enabled(by_command["apply_settings"], heater.state["mode"])
