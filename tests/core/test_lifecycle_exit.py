"""I-1.3 — every exit path stops the hardware.

There were zero process-level hooks in `src` before this: Ctrl-C, SIGTERM and
a Dock quit all ended the process with hardware enabled and ports open
(MANAGER-2/3, SERIAL-15, VIEW-TKINTER-8).
"""

import signal

import pytest

import lifecycle
from conftest import ManagedStub
from model.system_manager import SystemManager


@pytest.fixture(autouse=True)
def _clean_lifecycle():
    lifecycle._reset_for_tests()
    yield
    lifecycle._reset_for_tests()


def _manager_with(*names):
    manager = SystemManager()
    stubs = {n: ManagedStub(n) for n in names}
    for n, s in stubs.items():
        manager.register(n, s)
    return manager, stubs


def test_shutdown_stops_and_tears_down_every_model():
    manager, stubs = _manager_with("a", "b")
    lifecycle.set_current_manager(manager)
    lifecycle.shutdown("test")
    for s in stubs.values():
        assert s.stops == 1
        assert s.teardowns == 1


def test_shutdown_runs_only_once():
    """A signal arriving during atexit would otherwise tear down twice, the
    second pass against half-closed transports."""
    manager, stubs = _manager_with("a")
    lifecycle.set_current_manager(manager)
    lifecycle.shutdown("first")
    lifecycle.shutdown("second")
    assert stubs["a"].teardowns == 1


def test_shutdown_resolves_the_manager_when_it_fires_not_when_installed():
    """A launcher that captured its manager would keep stopping a stale one
    after a Web re-setup replaced it."""
    stale, stale_stubs = _manager_with("old")
    lifecycle.set_current_manager(stale)

    fresh, fresh_stubs = _manager_with("new")
    lifecycle.set_current_manager(fresh)

    lifecycle.shutdown("test")
    assert fresh_stubs["new"].teardowns == 1
    assert stale_stubs["old"].teardowns == 0


def test_shutdown_is_a_no_op_with_no_manager():
    lifecycle.shutdown("test")  # must not raise


def test_shutdown_survives_a_manager_that_raises():
    class Exploding:
        def shutdown_all(self):
            raise RuntimeError("registry is wedged")

    lifecycle.set_current_manager(Exploding())
    lifecycle.shutdown("test")  # must not raise and must not mask the exit


def test_install_exit_hooks_is_idempotent():
    lifecycle.install_exit_hooks()
    lifecycle.install_exit_hooks()
    assert signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None)


def test_sigterm_tears_down_before_the_process_dies(monkeypatch):
    """The handler stops the hardware, then lets the signal kill the process.

    The re-raise is intercepted here: calling the real one would deliver
    SIGTERM with the default action restored and take the pytest session
    down with it — which is itself the evidence that the handler does not
    swallow the signal.
    """
    manager, stubs = _manager_with("a")
    lifecycle.set_current_manager(manager)
    lifecycle.install_exit_hooks()

    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler)

    reraised = []
    monkeypatch.setattr(signal, "signal", lambda *a: None)
    monkeypatch.setattr(signal, "raise_signal", lambda s: reraised.append(s))

    handler(signal.SIGTERM, None)

    assert stubs["a"].stops == 1
    assert stubs["a"].teardowns == 1
    assert reraised == [signal.SIGTERM], "the signal must not be swallowed"
