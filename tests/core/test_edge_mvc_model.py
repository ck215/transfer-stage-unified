import time
import pytest
from unittest.mock import MagicMock
from model.probes import StepperProbe, DCProbe
from model.system_manager import SystemManager
from conftest import ManagedStub

def test_assign_corrupted_dict_to_ui_schema():
    """Test passing/assigning corrupted dictionaries to ui_schema."""
    probe = StepperProbe("SIM", None)
    corrupted_schema = {"broken": "schema"}
    
    # ui_schema is a property, so assigning should raise AttributeError
    with pytest.raises(AttributeError):
        probe.ui_schema = corrupted_schema

def test_modifying_missing_variables():
    """Test modifying missing variables on the model."""
    probe = StepperProbe("SIM", None)
    
    # Try setting a variable that doesn't exist in the class
    probe.non_existent_var = "Should not crash"
    assert probe.non_existent_var == "Should not crash"
    
    # Verify that this doesn't impact get_params
    params = probe.get_params()
    assert "non_existent_var" not in params

def test_mutating_state_out_of_order():
    """Test mutating state out of order checks exact internal variables instead of just avoiding crashes."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    # No forcing needed: the SIM transport acknowledges the enable, so the
    # transitions below succeed on their own merits (I-3.1).
    # Manual mode requires a bound pad (I-3.2), so the fixture has to
    # provide one. Before S7 this test reached MANUAL with no pad at all,
    # which is the state that energized coils for a mode nothing drove.
    probe.poller = MagicMock()
    probe.poller.gamepad = MagicMock()

    probe.enter_auton()
    assert probe.auton_flag is True
    assert probe.manual_flag is False
    
    probe.enter_manual()
    assert probe.auton_flag is False
    assert probe.manual_flag is True
    
    probe.macro_start_auton()
    assert probe.auton_flag is True
    assert probe.manual_flag is False
    assert probe.is_stepping is True
    
    probe.full_stop()
    assert probe.auton_flag is False
    assert probe.manual_flag is False
    assert probe.is_stepping is False

def test_mutually_exclusive_probe_flags():
    """Test that auton_flag and manual_flag can never be active at the same time."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    
    probe.enter_auton()
    assert not (probe.auton_flag and probe.manual_flag)
    
    probe.enter_manual()
    assert not (probe.auton_flag and probe.manual_flag)
    
    # The malformed state this used to simulate is no longer constructible.
    # Both flags are read-only views onto one mode (I-3.4), so the assignment
    # that produced "auton and manual at once" now raises instead of being
    # cleaned up afterwards by full_stop.
    with pytest.raises(AttributeError):
        probe.auton_flag = True
    with pytest.raises(AttributeError):
        probe.manual_flag = True

    probe.full_stop()
    assert not probe.auton_flag and not probe.manual_flag

def test_auto_disable_interlock_fires_with_no_view_attached():
    """The 5-minute idle interlock must live in the model so it protects
    every frontend, including the web dashboard (which previously had no
    auto-disable at all). No view/poller callback involved here at all."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    probe._INTERLOCK_POLL_INTERVAL = 0.02
    probe._INTERLOCK_TIMEOUT = 0.05

    probe.enable()
    assert probe.system_enabled is True

    time.sleep(0.3)

    assert probe.system_enabled is False
    probe.serial_comm.disable.assert_called()

def test_auto_disable_interlock_deferred_while_stepping():
    """Per user decision: defer the idle disable while actively stepping/
    manual, rather than disabling unconditionally like main does."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    probe._INTERLOCK_POLL_INTERVAL = 0.02
    probe._INTERLOCK_TIMEOUT = 0.05

    probe.macro_start_auton()

    time.sleep(0.3)

    # **Re-authored for S7.** This used to assert the opposite: that the
    # interlock was deferred while stepping. That deferral is the defect
    # (STEPPER-6, DC-1) — `is_stepping` was set by macro_start_auton and never
    # cleared on normal completion, so one autonomous move suppressed the idle
    # interlock for the rest of the session, in a mode that energizes coils.
    # The watchdog measures real inactivity now and defers on nothing.
    assert probe.system_enabled is False, "a stepping probe must still idle out"

def test_stepper_probe_step_size_defaults():
    """StepperProbe must default to step size 1, matching main/src/stepper_frame.py,
    not BaseProbe's 16 (a 16x-further-than-intended move on identical UI input)."""
    probe = StepperProbe("SIM", None)
    assert probe.x_step == "1"
    assert probe.y_step == "1"
    assert probe.z_step == "1"

def test_system_manager_rejects_an_invalid_model():
    """Registration is a contract boundary (RC-1).

    This used to accept the string "Not a model" and store it, because
    register_model was a bare dict write. A non-ManagedModel in the registry
    is a model that will be silently skipped at shutdown.
    """
    manager = SystemManager()
    with pytest.raises(TypeError):
        manager.register("Invalid", "Not a model")
    assert manager.get_model("Invalid") is None

def test_shutdown_all_tears_down_registered_models():
    manager = SystemManager()
    probe = ManagedStub("Stepper")
    manager.register("Stepper", probe)
    manager.shutdown_all()
    assert probe.teardowns == 1

def test_shutdown_all_stops_before_tearing_down():
    """RC-1 item 4: no model's teardown can skip the stop."""
    manager = SystemManager()
    probe = ManagedStub("Stepper", teardown_error=RuntimeError("wedged"))
    manager.register("Stepper", probe)
    manager.shutdown_all()
    assert probe.stops == 1
    assert probe.teardowns == 1

def test_release_tears_down_the_model_it_removes():
    """remove_model only removed; the documentation claimed it tore down too."""
    manager = SystemManager()
    probe = ManagedStub("Stepper")
    manager.register("Stepper", probe)
    manager.release("Stepper")
    assert manager.get_model("Stepper") is None
    assert probe.teardowns == 1

def test_dcprobe_mutating_state_out_of_order():
    """Test mutating state out of order on the DCProbe."""
    probe = DCProbe("SIM", None)
    probe.serial_comm = MagicMock()
    
    try:
        probe.disable()
        probe.enter_auton()
        probe.enable()
        probe.disable()
        probe.enter_manual()
    except Exception as e:
        pytest.fail(f"DCProbe crashed when mutating state out of order: {e}")

def test_dcprobe_invalid_speed():
    """Test DCProbe with invalid speed (assignment)."""
    probe = DCProbe("SIM", None)
    
    # Just checking it doesn't crash the program unexpectedly.
    try:
        probe.full_speed = "1000"
        probe.full_speed = "invalid_speed"
    except Exception:
        pass  # It's okay if it raises an exception, we just don't want a hard crash

from model.rotator_system import RotatorSystem
from model.schema import NeedsConfirmation

def test_rotator_state_code_map():
    """Test that the RotatorSystem correctly maps SMC100 state codes to human strings."""
    rotator = RotatorSystem()
    assert rotator._map_state_code("0A") == "Not referenced - run Home"
    assert rotator._map_state_code("33") == "Ready"
    assert rotator._map_state_code("1E") == "Homing"
    assert rotator._map_state_code("28") == "Moving"
    assert rotator._map_state_code("3C") == "Disabled"
    assert rotator._map_state_code("UNKNOWN") == "UNKNOWN"

def _rotator_with_stage():
    rotator = RotatorSystem()
    rotator.smc = MagicMock()
    return rotator


def test_rotator_moves_within_the_safe_range_without_asking():
    rotator = _rotator_with_stage()
    rotator.target_deg = "30"
    assert rotator.move_absolute() is True
    rotator.target_deg = "-30"
    assert rotator.move_absolute() is True


def test_rotator_asks_before_moving_past_the_safe_range():
    """**Re-authored for S10.** The check is a returned value now, not a callback.

    `_confirm_rotation` consulted `confirm_rotation_callback`, which the
    *views* injected into the model — and the Web client never injected one,
    so the branch these tests exercised was the "blocked automatically" one.
    A guard that silently declines is not a guard the operator can answer.
    """
    from model.schema import NeedsConfirmation

    rotator = _rotator_with_stage()
    rotator.target_deg = "30.1"
    result = rotator.move_absolute()
    assert isinstance(result, NeedsConfirmation)
    assert result.command == "move_absolute"
    assert "30.10" in result.prompt
    rotator.smc.move_absolute_deg.assert_not_called()


def test_rotator_proceeds_once_the_operator_confirms():
    rotator = _rotator_with_stage()
    rotator.target_deg = "40"
    assert isinstance(rotator.move_absolute(), NeedsConfirmation)
    assert rotator.move_absolute(confirmed=True) is True


def test_a_relative_move_is_judged_on_where_it_lands():
    """A small step from a large angle still leaves the safe range."""
    rotator = _rotator_with_stage()
    rotator.position = "29"
    rotator.step_deg = "5"
    assert isinstance(rotator.move_relative_positive(), NeedsConfirmation)

    rotator.step_deg = "1"
    assert rotator.move_relative_positive() is True


# --- ROTATOR-13: a disconnected rotator says so ---------------------------
#
# `home`, `move_*` and `reset_and_configure` all began `if self.smc:` and
# returned silently, so with no port (None/"SIM") every control was inert
# and nothing anywhere said why. There is no rotator simulator to fall back
# on: the honest answer is a refusal the operator can read.

def test_rotator_commands_refuse_when_there_is_no_stage():
    """Each schema command comes back Refused, with a reason, rather than
    returning None and being rendered as "executed"."""
    rotator = RotatorSystem(default_port="SIM")
    assert rotator.smc is None

    for command in ("home", "move_absolute", "move_relative_positive",
                    "move_relative_negative", "reset_and_configure"):
        result = rotator.execute_command(command)
        assert result.refused, f"{command} did not refuse: {result!r}"
        assert "connect" in result.reason.lower(), (
            f"{command} refused without saying why: {result.reason!r}")


def test_rotator_reports_no_connection_rather_than_simulation():
    """A SIM/None port does not make the rotator simulated — nothing
    simulates. `connection_status` is the model's own answer, so no view
    has to guess it from the port string."""
    assert RotatorSystem(default_port="SIM").connection_status == "disconnected"
    assert RotatorSystem(default_port=None).connection_status == "disconnected"
    assert RotatorSystem(default_port="None").connection_status == "disconnected"

    connected = _rotator_with_stage()
    connected.is_connected = True
    assert connected.connection_status == "hardware"


def test_rotator_commands_still_run_when_a_stage_is_present():
    """Guard for the ROTATOR-13 refusals: they must fire only on the
    no-stage path, not on every command."""
    rotator = _rotator_with_stage()
    rotator.is_connected = True
    assert rotator.execute_command("home").ok
    rotator.target_deg = "10"
    assert rotator.execute_command("move_absolute",
                                   inputs={"target_deg": "10"}).ok


# --- ROTATOR-11, model half: a move that did not finish is not a move ----

def test_a_failed_move_forgets_where_the_stage_was_going():
    """The driver's wait can end without the stage arriving — a timeout
    while it is still turning, a disabled state, a dead port — and none of
    those stop it. The commanded target must not survive as if the move had
    landed, or the next relative move is computed from a position the stage
    never reached (the same rule as safety-pattern.md item 6)."""
    import threading as _t

    rotator = _rotator_with_stage()
    reported = _t.Event()
    rotator.error_callback = lambda e: reported.set()
    rotator.smc.move_absolute_deg.side_effect = RuntimeError(
        "Wait timed out (last reported state 28); the stage has not been stopped")

    rotator.target_deg = "10"
    assert rotator.move_absolute() is True
    assert reported.wait(3), "the failing move never reported"

    assert rotator._commanded_target is None, (
        "a move that failed left its target behind as if it had arrived")

    # ...so the next relative move asks instead of assuming.
    rotator.step_deg = "1"
    assert isinstance(rotator.move_relative_positive(), NeedsConfirmation)


def test_a_successful_move_keeps_its_target():
    """Guard: only a *failed* move forgets. Dropping the target after every
    move would put the ±30° guard back on the polled position, which is
    the ROTATOR-4 defect."""
    import time as _t

    rotator = _rotator_with_stage()
    rotator.target_deg = "10"
    assert rotator.move_absolute() is True
    for _ in range(200):
        if rotator.smc.move_absolute_deg.called:
            break
        _t.sleep(0.01)
    assert rotator._commanded_target == 10.0
