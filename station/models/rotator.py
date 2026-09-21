"""SKELETON - to be implemented. See REBUILD_BRIEF.md.

The member names below are the contract (design.json). Private helpers may be
added; public names may not change without the lead.
"""
from station.model import Model

class Rotator(Model):
    """Was RotatorSystem. Owns an SMC100.
    
    Absorbs: RotatorSystem
    
    MUST SATISFY:
    [CARRY] Tubing confirmation beyond 30 degrees on every move type. A stop
    forgets the commanded target. Busy guard against stacked moves. (home()
    must commit its target only after the guard passes: review finding 2.)
    (ROTATOR-4)
    """

    @property
    def fault(self):
        """was RotatorSystem.error
        one fault field on every model
        """
        raise NotImplementedError

    @property
    def motion_state(self):
        """was RotatorSystem.state
        'state' is the Model snapshot
        """
        raise NotImplementedError

    @property
    def position(self):
        """was RotatorSystem.position
        setter purged; written only by _poll under the lock
        """
        raise NotImplementedError

    def __init__(self, *args, **kwargs):
        """was RotatorSystem.__init__
        """
        raise NotImplementedError

    def configure(self, *args, **kwargs):
        """was RotatorSystem.reset_and_configure
        """
        raise NotImplementedError

    def home(self, *args, **kwargs):
        """was RotatorSystem.home
        target committed only after the guard passes (designs out finding 2)
        """
        raise NotImplementedError

    def move_by(self, *args, **kwargs):
        """was RotatorSystem.move_relative_positive,
        RotatorSystem.move_relative_negative
        one command with a signed step
        """
        raise NotImplementedError

    def move_to(self, *args, **kwargs):
        """was RotatorSystem.move_absolute
        """
        raise NotImplementedError

    def _commit_target(self, *args, **kwargs):
        """was RotatorSystem._commit_target
        """
        raise NotImplementedError

    def _forget_target(self, *args, **kwargs):
        """was RotatorSystem._forget_target
        """
        raise NotImplementedError

    def _motion_state_name(self, *args, **kwargs):
        """was RotatorSystem._map_state_code
        """
        raise NotImplementedError

    def _move_guarded(self, *args, **kwargs):
        """was RotatorSystem._guarded_move
        """
        raise NotImplementedError

    def _poll(self, *args, **kwargs):
        """was RotatorSystem.poll_status
        """
        raise NotImplementedError

    def _reference_position(self, *args, **kwargs):
        """was RotatorSystem._reference_position
        """
        raise NotImplementedError

    def _run_motion(self, *args, **kwargs):
        """was RotatorSystem._run_async, RotatorSystem._async_wrapper,
        RotatorSystem._run_guarded
        three nested wrappers become one; the latch check moves inside
        SMC100's port lock via abort_if
        """
        raise NotImplementedError

    def _sample_loop(self, *args, **kwargs):
        """was RotatorSystem._sample_loop
        """
        raise NotImplementedError
