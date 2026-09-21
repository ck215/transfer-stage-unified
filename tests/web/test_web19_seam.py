"""WEB-19 / D-8: the two halves of the liveness gate actually meet.

The browser half and the `probes.py` watchdog were built in parallel
worktrees that could not see each other. They agreed on the design and
disagreed on the name — `touch_client_heartbeat` versus
`touch_client_liveness` — and because the call is a duck-typed `getattr`
against a constant, the mismatch produced no error anywhere. Heartbeats
resolved to `None`, `last_client_seen_time` stayed unset, and the FULL STOP
that D-8 exists to deliver could never fire.

Both halves' own tests passed. Each one mocked the other.

This file is the seam test: it asserts the constant names a method that
really exists on the real class, and that calling it through the adapter's
own lookup actually arms the model.
"""
from unittest.mock import MagicMock

from model.probes import BaseProbe
from views.web.web_adapter import WebModelAdapter


def test_web19_seam_is_joined():
    """The hook constant must name a method BaseProbe actually defines."""
    hook = WebModelAdapter.CLIENT_HEARTBEAT_HOOK
    assert hasattr(BaseProbe, hook), (
        f"WebModelAdapter.CLIENT_HEARTBEAT_HOOK is {hook!r}, which BaseProbe "
        f"does not define — every heartbeat silently resolves to None and "
        f"D-8's gate never arms on any device")
    assert callable(getattr(BaseProbe, hook))


def test_the_hook_actually_arms_the_deadline():
    """Going through the adapter's own lookup must set the model's clock.

    Asserting the name exists is not enough: the point of the seam is that
    a heartbeat moves `last_client_seen_time` off `None`, because that is
    the value `_check_client_liveness` gates on.
    """
    probe = BaseProbe.__new__(BaseProbe)
    probe.last_client_seen_time = None
    probe._client_liveness_warned = True

    hook = getattr(probe, WebModelAdapter.CLIENT_HEARTBEAT_HOOK, None)
    assert hook is not None, "the adapter's lookup found nothing on the model"
    hook()

    assert probe.last_client_seen_time is not None, (
        "a heartbeat did not arm the deadline, so the watchdog still reads "
        "this device as a desktop session with no web client")
    assert probe._client_liveness_warned is False, (
        "a fresh heartbeat must clear a pending warning, or the operator "
        "keeps being told about a client that has come back")


def test_a_model_without_the_hook_is_simply_skipped():
    """Not every model is a probe. The lookup must stay tolerant."""
    plain = MagicMock(spec=[])
    assert getattr(plain, WebModelAdapter.CLIENT_HEARTBEAT_HOOK, None) is None
