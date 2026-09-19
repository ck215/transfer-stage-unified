from typing import Protocol, runtime_checkable


@runtime_checkable
class ManagedModel(Protocol):
    """Lifecycle contract SystemManager relies on. Every model class
    SystemManager can register should implement both methods explicitly
    (even as a thin wrapper over existing behavior) instead of SystemManager
    guessing across a shifting set of duck-typed method names.
    """

    def teardown(self) -> None:
        """Full, graceful shutdown: stop all background activity and release
        hardware connections. Called on normal removal, reboot, or app
        shutdown — not time-critical, should not raise."""
        ...

    def emergency_stop(self) -> None:
        """Halt hardware activity immediately, by the strongest means this
        model has available. Called by the global FULL STOP control — must
        be fast and must never block."""
        ...
