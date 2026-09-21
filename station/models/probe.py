"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
import enum

from station.model import Model

class ProbeMode(enum.Enum):
    """DISABLED, IDLE (was ENABLED_IDLE), AUTO (was AUTONOMOUS), MANUAL, FAULT.
    """
    DISABLED = "disabled"
    IDLE = "idle"
    AUTO = "autonomous"
    MANUAL = "manual"
    FAULT = "fault"


class Probe(Model):
    """Mode machine, move/jog frames, position sampling, motion interlock. Owns
    a SerialPort and a Gamepad. Was BaseProbe (1682 lines); loses scripts,
    client liveness, gamepad plumbing and its private copy of the latch.
    
    Absorbs: <model.probes>, BaseProbe, serial
    
    MUST SATISFY:
    [OUT] The jog loop and the gamepad belong to the model, so manual mode
    works identically under all three views, the Web included.  (GAMEPAD-1,
    DC-5, STEPPER-2, WEB-2, WEB-5, MANAGER-13, VIEW-TKINTER-5)
    [CARRY] is_moving expires by deadline. The idle interlock fires in AUTO
    and MANUAL on real inactivity (D-3). Restart race: a new watchdog never
    reuses a dying thread's stop event.  (DC-1, STEPPER-6, STEPPER-7, VIEW-
    TKINTER-3)
    [CARRY] Step stays available while AUTO so repeated stepping works;
    refused only while is_moving. (This was closed for the Web in the ledger
    and re-broken in the schema: review finding 5.)  (DC-6)
    """

    @property
    def can_kill_coils(self):
        """was BaseProbe.supports_coil_kill
        """
        raise NotImplementedError

    @property
    def is_auto(self):
        """was BaseProbe.auton_flag
        """
        raise NotImplementedError

    @property
    def is_enabled(self):
        """was BaseProbe.system_enabled
        """
        raise NotImplementedError

    @property
    def is_manual(self):
        """was BaseProbe.manual_flag
        """
        raise NotImplementedError

    @property
    def is_moving(self):
        """was BaseProbe.is_stepping
        """
        raise NotImplementedError

    @property
    def mode(self):
        """was BaseProbe.mode
        """
        raise NotImplementedError

    @property
    def velocity(self):
        """was BaseProbe.vel_x, BaseProbe.vel_y, BaseProbe.vel_z
        one (x, y, z) property
        """
        raise NotImplementedError

    def __init__(self, *args, **kwargs):
        """was BaseProbe.__init__
        """
        raise NotImplementedError

    def set_mode(self, *args, **kwargs):
        """was BaseProbe.toggle_manual, BaseProbe.toggle_auton,
        BaseProbe.enter_auton, BaseProbe.enter_manual,
        BaseProbe.toggle_enable, BaseProbe.full_stop
        six public ways to change mode become one; schema toggles pass the
        target mode as an argument
        """
        raise NotImplementedError

    def step(self, *args, **kwargs):
        """was BaseProbe.macro_start_auton
        stays available while AUTO so repeated steps work (finding 5)
        """
        raise NotImplementedError

    def _axis_state(self, *args, **kwargs):
        """was BaseProbe._axis_state
        """
        raise NotImplementedError

    def _deenergize(self, *args, **kwargs):
        """was BaseProbe._go_disabled, serial.disable
        """
        raise NotImplementedError

    def _energize(self, *args, **kwargs):
        """was BaseProbe._arm, serial.enable
        the 'e' control byte belongs to the probe
        """
        raise NotImplementedError

    def _frame(self, *args, **kwargs):
        """was BaseProbe.get_params
        """
        raise NotImplementedError

    def _gated_param(self, *args, **kwargs):
        """was <model.probes>._mode_gated_param
        """
        raise NotImplementedError

    def _halt_hardware(self, *args, **kwargs):
        """was BaseProbe.send_stop_command, BaseProbe.power_down,
        BaseProbe._report_power_down_unsupported
        zero frame, 'd', and the coil-kill report in one method, all on the
        priority lane (designs out finding 1)
        """
        raise NotImplementedError

    def _jog_loop(self, *args, **kwargs):
        """was BaseProbe._input_loop
        """
        raise NotImplementedError

    def _mark_moving(self, *args, **kwargs):
        """was BaseProbe._begin_stepping
        """
        raise NotImplementedError

    def _note_position(self, *args, **kwargs):
        """was BaseProbe._note_position
        """
        raise NotImplementedError

    def _read_position(self, *args, **kwargs):
        """was BaseProbe.read_position, serial.read_position
        parsing a probe reply is the probe's job
        """
        raise NotImplementedError

    def _sample_loop(self, *args, **kwargs):
        """was BaseProbe._sample_loop
        """
        raise NotImplementedError

    def _send_jog(self, *args, **kwargs):
        """was BaseProbe.send_manual_mode_command,
        serial.send_manual_mode_command
        """
        raise NotImplementedError

    def _send_move(self, *args, **kwargs):
        """was BaseProbe.send_autonomous_command,
        serial.send_autonomous_command
        goes through SerialPort.write like every other byte (designs out
        finding 7)
        """
        raise NotImplementedError

    def _set_mode(self, *args, **kwargs):
        """was BaseProbe._transition
        """
        raise NotImplementedError

    def _start_interlock(self, *args, **kwargs):
        """was BaseProbe._start_interlock_watchdog
        """
        raise NotImplementedError

    def _stop_interlock(self, *args, **kwargs):
        """was BaseProbe._stop_interlock_watchdog
        """
        raise NotImplementedError

    def _touch_activity(self, *args, **kwargs):
        """was BaseProbe.touch_activity
        """
        raise NotImplementedError


class StepperProbe(Probe):
    """TMC2209 board.
    
    Absorbs: StepperProbe
    """

    def __init__(self, *args, **kwargs):
        """was StepperProbe.__init__
        """
        raise NotImplementedError


class DCProbe(Probe):
    """Own packet format; firmware cannot kill coils.
    
    Absorbs: DCProbe
    """

    def __init__(self, *args, **kwargs):
        """was DCProbe.__init__
        """
        raise NotImplementedError

    def _frame(self, *args, **kwargs):
        """was DCProbe.get_params
        """
        raise NotImplementedError


class ChuckPositioner(Probe):
    """Stepper-family board.
    
    Absorbs: ChuckPositioner
    """

    def __init__(self, *args, **kwargs):
        """was ChuckPositioner.__init__
        """
        raise NotImplementedError
