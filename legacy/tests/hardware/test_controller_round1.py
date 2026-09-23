import contextlib
import pytest
import sys
import time
from unittest.mock import MagicMock, patch
from error_routing import ErrorRouter
from controller.serial import serial, PACKET_FORMAT
from controller.gamepad import ControllerPoller, BaseGamepad, XboxGamepad, get_gamepad_wrapper


@contextlib.contextmanager
def patched_sdl(count=2, name="Controller"):
    """Patch SDL where it now lives: controller/input_service.py (RC-13).

    Tests used to patch `controller.gamepad.pygame`, because every poller
    talked to SDL directly. SDL has one owner now, so that is the module to
    patch, and `input_service` is reset between tests so one test's acquired
    handles cannot leak into the next.
    """
    from controller import input_service as svc

    svc.input_service._handles.clear()
    svc.input_service._initialised = False
    with patch("controller.input_service.pygame") as mock_pygame:
        mock_pygame.joystick.get_count.return_value = count
        mock_pygame.joystick.get_init.return_value = True
        js = MagicMock()
        js.get_name.return_value = name
        mock_pygame.joystick.Joystick.return_value = js
        try:
            yield mock_pygame
        finally:
            svc.input_service._handles.clear()
            svc.input_service._initialised = False



# ==========================================
# 1. Disconnected Joystick & Reconnect Tests
# ==========================================


def _bare_poller():
    """A poller assembled without touching hardware.

    __init__ acquires a real device, so these tests build the object directly.
    _init_input_state() sets up the latched-input fields (RC-13 item 2) that
    __init__ would otherwise install.
    """
    poller = ControllerPoller.__new__(ControllerPoller)
    poller._init_input_state()
    poller._thread = None
    poller._closed = False
    return poller


def test_poller_initialization_with_none_or_invalid_id():
    """Verify initializing poller with None, 'None', or 'Virtual' sets claim to 'None Detected'."""
    claims = {}
    poller = ControllerPoller(None, claims, "TestProcess")
    assert poller.gamepad is None
    assert claims["TestProcess"] == "None Detected"

    poller2 = ControllerPoller("Virtual-Controller", claims, "TestProcess2")
    assert poller2.gamepad is None
    assert claims["TestProcess2"] == "None Detected"


def test_poller_os_disconnect_during_poll_loop():
    """Verify that OS disconnection during polling triggers _handle_disconnect and clears gamepad."""
    claims = {"TestProcess": 0}
    poller = _bare_poller()
    poller.process_name = "TestProcess"
    poller.active_claims = claims
    poller.controllerID = 0
    poller.controller_index = 0
    poller.gamepad = MagicMock()
    poller.is_polling = True
    poller.gui_root = None
    poller.log_updater = None
    poller.activity_callback = None

    with patch.object(ControllerPoller, "_is_os_connected", return_value=False):
        with patch.object(ErrorRouter, "report_warning") as mock_warn:
            poller._poll_loop()
            assert poller.gamepad is None
            assert poller.is_polling is False
            assert claims["TestProcess"] == "None Detected"
            mock_warn.assert_called_once()


def test_poller_pygame_error_during_get_mapped_state():
    """Verify pygame.error during hardware read resets gamepad gracefully."""
    import pygame
    poller = _bare_poller()
    poller.is_polling = True
    poller.gamepad = MagicMock()
    poller.gamepad.get_mapped_state.side_effect = pygame.error("Joystick hardware removed")

    with patch.object(ErrorRouter, "report_error") as mock_err:
        state = poller.get_mapped_state()
        assert state == {}
        assert poller.gamepad is None
        assert poller.is_polling is False
        mock_err.assert_called_once()


def test_poller_reconnect_flow():
    """Verify re-initialization re-acquires a device via set_controller.

    Was written against `connect_controller`, deleted in GAMEPAD-17: it had
    no callers in src (grep-verified) and would call pygame.quit()
    unconditionally, the process-wide teardown RC-13 built InputService to
    eliminate. `set_controller` is the live duplicate — same
    re-initialization path, without the SDL-wide teardown.
    """
    claims = {}
    with patched_sdl() as mock_pygame:
        mock_pygame.joystick.get_count.return_value = 1
        mock_js = MagicMock()
        mock_js.get_name.return_value = "Controller (Xbox One For Windows)"
        mock_pygame.joystick.Joystick.return_value = mock_js

        poller = _bare_poller()
        poller.process_name = "ProbeA"
        poller.active_claims = claims
        poller.controllerID = 0
        poller.gamepad = None
        poller.is_polling = False
        poller.gui_root = None
        poller.log_updater = None
        poller.activity_callback = None

        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            success = poller.set_controller(0)
            assert success is True
            assert poller.gamepad is not None
            assert claims["ProbeA"] == 0


def test_get_mapped_state_when_disconnected():
    """Verify get_mapped_state returns empty dict when gamepad is None or unpolled."""
    poller = _bare_poller()
    poller.gamepad = None
    poller.is_polling = False
    assert poller.get_mapped_state() == {}


# ==========================================
# 2. Serial Command Dispatching & Routes
# ==========================================

def test_serial_auton_command_dispatch():
    """Verify autonomous 12-field command string dispatch.

    SERIAL-6: `serial(...)` used to run its identity handshake synchronously
    inside the constructor, so this test could count on exactly one `s\\n`
    ping having already landed in `write.call_args_list[0]` before the auton
    command sent the second call. Now the handshake runs on a background
    thread that does not even start pinging until `BOOTLOADER_WAIT` has
    elapsed, so this asserts on the *last* write call rather than a fixed
    position or count -- the number of handshake pings that have or have not
    landed by the time this line runs is not this test's business.
    """
    with patch("controller.serial.pyserial.Serial") as mock_serial_cls:
        mock_inst = MagicMock()
        mock_inst.is_open = True
        mock_serial_cls.return_value = mock_inst

        s = serial("COM1")
        params = {
            'x_step_size': 100,
            'y_step_size': 200,
            'z_step_size': 300,
            'full_speed': 1000,
            'slow_speed': 100,
            'brake_distance': 50,
            'x_dist': 5.0,
            'y_dist': 10.0,
            'z_dist': 2.0,
            'command_code_manual': 0,
            'command_code_auton': 1,
        }
        s.send_autonomous_command(params)
        assert mock_inst.write.call_count >= 1
        sent_bytes = mock_inst.write.call_args_list[-1][0][0]
        expected_str = "100,200,300,0,1000,100,50,5.0,10.0,2.0,0,1\n"
        assert sent_bytes == expected_str.encode('utf-8')






def test_serial_enable_disable_disconnected():
    """A *disconnected real port* refuses to arm. Simulator mode does not.

    Re-authored in S3 (SERIAL-9). This previously asserted that SIM raised
    "Arduino not detected" — which was the bug, not the contract: simulator
    mode exists precisely to exercise the bench with no hardware attached,
    and that ValueError made every SIM probe permanently un-armable.
    """
    sim = serial("SIM")
    sim.enable()
    sim.disable()

    dead = serial("SIM")
    dead.SERIAL_PORT = "/dev/ttyUSB0"   # a real port name...
    dead.ser = None                      # ...that never opened
    with pytest.raises(ValueError, match="Arduino not detected"):
        dead.enable()
    with pytest.raises(ValueError, match="Arduino not detected"):
        dead.disable()


# ==========================================
# 3. Corrupt Serial Data & Buffer Handling
# ==========================================

def test_serial_read_position_corrupt_text_and_floats():
    """Verify corrupt/malformed POS strings are handled without crashing."""
    with patch("controller.serial.pyserial.Serial") as mock_serial_cls:
        mock_inst = MagicMock()
        mock_inst.is_open = True
        mock_serial_cls.return_value = mock_inst

        s = serial("COM1")

        # Non-numeric floats
        mock_inst.in_waiting = 20
        mock_inst.read.return_value = b"POS:abc,def,ghi\n"
        assert s.read_position() is None

        # Truncated POS string
        mock_inst.in_waiting = 15
        mock_inst.read.return_value = b"POS:10,20\n"
        assert s.read_position() is None

        # Too many fields
        mock_inst.in_waiting = 25
        mock_inst.read.return_value = b"POS:10,20,30,40\n"
        assert s.read_position() is None


def test_serial_read_position_buffer_overflow_prevention():
    """Verify read_position caps buffer size under heavy corrupt stream."""
    with patch("controller.serial.pyserial.Serial") as mock_serial_cls:
        mock_inst = MagicMock()
        mock_inst.is_open = True
        mock_serial_cls.return_value = mock_inst

        s = serial("COM1")
        s._read_buffer = "X" * 1500
        mock_inst.in_waiting = 0

        s.read_position()
        assert len(s._read_buffer) <= 512


def test_serial_read_position_multiline_stream():
    """Verify reading multiple POS lines returns the latest valid reading."""
    with patch("controller.serial.pyserial.Serial") as mock_serial_cls:
        mock_inst = MagicMock()
        mock_inst.is_open = True
        mock_serial_cls.return_value = mock_inst

        s = serial("COM1")
        mock_inst.in_waiting = 50
        mock_inst.read.return_value = b"POS:100,200,300\nGARBAGE\nPOS:400,500,600\n"

        pos = s.read_position()
        assert pos == (400, 500, 600)


# ==========================================
# 4. Exception Interceptors & ErrorRouter
# ==========================================

def test_a_subscriber_receives_every_severity():
    """Replaces `test_error_router_custom_callbacks`.

    That test pinned `set_callbacks(err, warn, info)` — three process-global
    slots where installing a second view silently replaced the first. RC-8
    made subscription the mechanism, so the property under test became "a
    subscriber sees what was published" rather than "the callback slot was
    invoked".
    """
    from error_routing import ErrorRouter, bus

    seen = []
    ErrorRouter.subscribe(seen.append)
    try:
        test_exc = RuntimeError("Test Exception")
        ErrorRouter.report_error("ErrTitle", "ErrMessage 123", test_exc)
        ErrorRouter.report_warning("WarnTitle", "WarnMessage 123")
        ErrorRouter.report_info("InfoTitle", "InfoMessage 123")
    finally:
        ErrorRouter.unsubscribe(seen.append)

    assert [e.severity for e in seen] == ["error", "warning", "info"]
    assert [e.title for e in seen] == ["ErrTitle", "WarnTitle", "InfoTitle"]
    assert seen[0].exception is test_exc
    # Monotonic and gapless, which is what `/api/errors?since=` rides on.
    assert [e.id for e in seen] == [1, 2, 3]


def test_a_second_subscriber_does_not_silence_the_first():
    """The defect `set_callbacks` made unavoidable: starting the web view
    alongside a desktop view replaced the desktop view's callbacks, so the
    desktop stopped reporting entirely."""
    from error_routing import ErrorRouter

    first, second = [], []
    ErrorRouter.subscribe(first.append)
    ErrorRouter.subscribe(second.append)
    try:
        ErrorRouter.report_error("Title", "Message")
    finally:
        ErrorRouter.unsubscribe(first.append)
        ErrorRouter.unsubscribe(second.append)

    assert len(first) == 1 and len(second) == 1


def test_a_broken_subscriber_does_not_stop_the_others():
    from error_routing import ErrorRouter

    def explodes(event):
        raise RuntimeError("subscriber is broken")

    survived = []
    ErrorRouter.subscribe(explodes)
    ErrorRouter.subscribe(survived.append)
    try:
        ErrorRouter.report_error("Title", "Message")
    finally:
        ErrorRouter.unsubscribe(explodes)
        ErrorRouter.unsubscribe(survived.append)

    assert len(survived) == 1


def test_repeats_fold_into_one_event_with_a_count():
    """Replaces `test_error_router_spam_suppression`, and inverts it.

    The old rate limit keyed on the **message text** and *dropped* the
    repeat, so a fault that persisted for a minute left one line in the log
    and no indication it had recurred — the log lied about duration. I-8.2
    asks for one event and a visible state. The key is
    `(severity, source, title)` now and the repeat increments a count.
    """
    from error_routing import ErrorRouter, bus

    seen = []
    ErrorRouter.subscribe(seen.append)
    try:
        first = ErrorRouter.report_error("Title", "Repeated Message")
        again = ErrorRouter.report_error("Title", "Repeated Message")
        # Same title and source, *different* text: still the same condition,
        # where the old text key would have treated it as brand new.
        third = ErrorRouter.report_error("Title", "Repeated, reworded")
        other = ErrorRouter.report_error("Another Title", "Different Message")
    finally:
        ErrorRouter.unsubscribe(seen.append)

    assert first is again is third
    assert first.count == 3
    assert other is not first
    # One notification per *event*, not per report.
    assert len(seen) == 2
    assert len(bus.since(0)) == 2


def test_rate_limits_are_keyed_per_severity():
    """A warning and an error sharing a title are different conditions."""
    from error_routing import ErrorRouter, bus

    ErrorRouter.report_warning("Overheat", "approaching limit")
    ErrorRouter.report_error("Overheat", "limit exceeded")
    assert len(bus.since(0)) == 2


def test_with_no_subscriber_the_bus_prints(capsys):
    """Replaces `test_error_router_default_fallback_printing`.

    The old fallback ran *after* the text dedup, so a repeated message was
    silently dropped before anyone checked whether a callback existed —
    ERRORS-10, a report lost with no UI involved at all.
    """
    from error_routing import ErrorRouter, bus

    assert bus.subscriber_count == 0

    ErrorRouter.report_error("FallbackErr", "Unique Err Text 999")
    assert "[ERROR] app/FallbackErr: Unique Err Text 999" in capsys.readouterr().out

    ErrorRouter.report_warning("FallbackWarn", "Unique Warn Text 999")
    assert "[WARNING] app/FallbackWarn: Unique Warn Text 999" in capsys.readouterr().out

    ErrorRouter.report_info("FallbackInfo", "Unique Info Text 999")
    assert "[INFO] app/FallbackInfo: Unique Info Text 999" in capsys.readouterr().out


def test_publishing_from_many_threads_loses_nothing():
    """`ErrorRouter` had no lock at all. Every mutation is under one now,
    and subscribers are invoked outside it so a subscriber that publishes
    cannot deadlock."""
    import threading
    from error_routing import EventBus

    local = EventBus(max_events=10000)
    seen = []
    lock = threading.Lock()

    def collect(event):
        with lock:
            seen.append(event.id)

    local.subscribe(collect)

    def publish_many(worker):
        for i in range(50):
            local.publish("info", f"worker-{worker}", f"title-{i}", "msg")

    threads = [threading.Thread(target=publish_many, args=(w,))
               for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(local.since(0)) == 400
    assert len(seen) == 400
    assert len(set(seen)) == 400, "ids must be unique across threads"
