"""DC-5 and MANAGER-13: Web probe manual mode starts the polling loop.

DC-5 claimed Web has no manual-mode input routing. The fix was RC-4 (src/model/probes.py),
moving the input pump and poller startup into BaseProbe.start_loops(), called from
_transition() when entering any mode (manual or autonomous). Every frontend reaches
this through the same path — including the Web dashboard, which used to have no input
pump of its own at all.

MANAGER-13 has two parts:
1. Same as DC-5 — Web doesn't start polling/route manual input.
2. Dead loop in WebDashboardWindow.__init__ iterates an empty manager.

The dead loop is fixed by not calling it (the real wiring is in _initialize_setup_locked).
This test verifies the first part: that when a Web probe enters manual mode via the
adapter's dispatch_command, it actually starts polling and the input pump thread.
"""

import threading
import time
from unittest.mock import MagicMock, patch
import pytest
from model.probes import StepperProbe, ProbeMode
from views.web.web_adapter import WebModelAdapter


def test_web_dispatch_toggle_manual_starts_polling():
    """Toggling manual mode via the Web adapter starts the poller.

    This is DC-5: the Web path reaches the same _transition method that
    starts the loops. The poller is attached to the model at construction
    (BaseProbe.__init__), and _transition calls start_loops() which calls
    poller.start_polling().
    """
    # Construct a real probe model with a mock serial port.
    mock_serial = MagicMock()

    probe = StepperProbe(mock_serial, controller_id="0")

    # The model has a poller (created in __init__).
    assert probe.poller is not None

    # Initially not polling.
    assert not probe.poller.is_polling

    # Mock the poller to have a gamepad so the model thinks a controller is present.
    mock_gamepad = MagicMock()
    mock_gamepad.gamepad = MagicMock()
    probe.poller.gamepad = MagicMock()  # Simulate a bound gamepad

    # Dispatch toggle_manual via the Web adapter.
    adapter = _adapter_with(probe, "TestDevice1")
    result = adapter.dispatch_command("TestDevice1", "toggle_manual")

    # Verify the command succeeded.
    assert result["status"] == "ok"

    # Verify manual mode is entered.
    assert probe.manual_flag

    # Verify the poller is now polling.
    assert probe.poller.is_polling, (
        "DC-5: poller must be running when entering manual mode, "
        "so gamepad state reaches the model")

    # Verify the input thread is alive.
    assert probe._input_thread is not None
    assert probe._input_thread.is_alive(), (
        "DC-5: input pump thread must be alive to forward gamepad state")

    # Cleanup: leave manual mode.
    probe.full_stop()
    time.sleep(0.05)  # Let threads settle.


def test_web_dispatch_toggle_auton_starts_polling():
    """Toggling autonomous mode via the Web adapter also starts the poller.

    This is a regression guard: both manual and autonomous modes should start
    the loops. The fix applies to both paths.
    """
    mock_serial = MagicMock()

    probe = StepperProbe(mock_serial, controller_id="1")
    assert not probe.poller.is_polling

    adapter = _adapter_with(probe, "TestDevice2")
    result = adapter.dispatch_command("TestDevice2", "toggle_auton")

    assert result["status"] == "ok"
    assert probe.auton_flag

    # The sample thread should be alive (polling position).
    assert probe._sample_thread is not None
    assert probe._sample_thread.is_alive(), (
        "entering autonomous mode must start the sample loop")

    probe.full_stop()
    time.sleep(0.05)


def test_web_manual_mode_input_pump_runs():
    """The input pump thread actually runs and calls send_manual_mode_command.

    This is DC-5 part 2: the Web path has no polling loop code; the fix is that
    the model owns the loop and every path (Web included) reaches it. This test
    verifies the pump runs through the Web path.
    """
    mock_serial = MagicMock()

    probe = StepperProbe(mock_serial, controller_id="2")
    probe.poller.gamepad = MagicMock()  # Simulate a bound gamepad

    # Mock send_manual_mode_command to count calls.
    with patch.object(probe, "send_manual_mode_command") as mock_send:
        # Set up poller to return a non-empty gamepad state.
        probe.poller.get_mapped_state = MagicMock(return_value={"x": 0.5})

        # Enter manual mode via the Web adapter.
        adapter = _adapter_with(probe, "TestDevice3")
        adapter.dispatch_command("TestDevice3", "toggle_manual")

        # Let the pump run for a few cycles (~0.1 s at 20 Hz).
        time.sleep(0.15)

        # Verify send_manual_mode_command was called (at least once).
        assert mock_send.call_count > 0, (
            "DC-5: input pump must call send_manual_mode_command "
            "when manual_flag is true")

    probe.full_stop()
    time.sleep(0.05)


def test_web_model_inherits_poller_from_model_init():
    """The model-owned poller starts in stop_polling state.

    This is a seam check: the poller is created in BaseProbe.__init__,
    _not_ by the Web layer. The Web layer reaches it only through the model's
    public API (toggle_manual, toggle_auton, etc.), which calls _transition,
    which calls start_loops.
    """
    mock_serial = MagicMock()
    probe = StepperProbe(mock_serial, controller_id="3")

    # The poller exists.
    assert probe.poller is not None

    # It is not polling initially.
    assert not probe.poller.is_polling

    # When we enter manual mode, it starts polling (not a dead model state).
    probe.poller.gamepad = MagicMock()  # Simulate a bound gamepad
    probe.enter_manual()
    assert probe.poller.is_polling

    probe.full_stop()
    time.sleep(0.05)


def _adapter_with(model, device_name="TestDevice"):
    """Construct a minimal adapter with only the collaborators it reaches."""
    import threading
    adapter = WebModelAdapter.__new__(WebModelAdapter)
    adapter._state_lock = threading.RLock()
    adapter._device_locks = {}
    adapter._generation = 0
    adapter.log_buffer = []
    adapter.system_manager = MagicMock()
    adapter.system_manager.get_active_models_snapshot.return_value = {
        device_name: model,
    }
    return adapter
