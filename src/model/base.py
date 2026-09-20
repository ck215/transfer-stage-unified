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


class SchemaCommands:
    """The command contract every schema-driven model shares (RC-7, D-5).

    One implementation, mixed into all four models, so that "run this command
    with these field values" cannot mean something subtly different per model
    — which is how the same button came to behave differently in each
    frontend.

    A model using this declares `PARAMS`, a `{name: Param}` table.
    """

    PARAMS = {}

    def apply_inputs(self, inputs):
        """Validate a whole set of operator values and commit them, or none.

        Returns `(ok, error_or_None)`.

        **Atomic on purpose.** Committing field by field lets a frame go out
        built from a mix of new and old values — the stale-value class D-5
        exists to kill. Tk papered over it by calling `focus_set()` before
        every command to force a pending edit to commit: a named anti-fix,
        because it only worked in Tk, only for the widget that happened to
        hold focus, and not at all for the Web client.

        Refusal names the offending *field*, so the operator is told which box
        is wrong instead of watching the command quietly do nothing.
        """
        if not inputs:
            return True, None

        parsed = {}
        for name, raw in inputs.items():
            param = self.PARAMS.get(name)
            if param is None:
                return False, (f"{name} is not a parameter of "
                               f"{self.__class__.__name__}")
            ok, value = param.parse(raw)
            if not ok:
                return False, value
            parsed[name] = value

        for name, value in parsed.items():
            setattr(self, name, value)
        return True, None

    def execute_command(self, name, inputs=None, args=None):
        """Run a schema command with the widget values it declared.

        The single entry point all three views use, so the validate-then-run
        ordering is the same everywhere. `args` carries what a composite
        supplies — a chosen file path, a selected region — which the operator
        produced through a dialog rather than a field.
        """
        ok, error = self.apply_inputs(inputs)
        if not ok:
            print(f"[{self.__class__.__name__}] {name} refused: {error}")
            try:
                from error_routing import ErrorRouter
                ErrorRouter.report_warning("Invalid Input", error)
            except Exception:
                pass
            return False
        command = getattr(self, name, None)
        if not callable(command):
            raise AttributeError(
                f"{self.__class__.__name__} has no command {name!r}")
        return command(*(args or ()))
