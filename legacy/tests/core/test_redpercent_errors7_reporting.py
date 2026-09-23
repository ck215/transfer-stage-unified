"""ERRORS-7 (redpercent_system.py share).

Ledger row ERRORS-7 is "errors are printed to a console nobody watches
instead of routed to the operator". `probes.py`, `serial.py` and
`temperature_system.py` are closed; `rotator_system.py` is the lead's;
this is `redpercent_system.py`'s.

Sites fixed here, each print-only or entirely silent before this change:

  - `save_log`'s "no data" guard: reached only when the operator explicitly
    clicked Save Log (the `file_save` composite) and nothing happened.
  - `save_log`'s success path: an explicit, operator-triggered save with no
    confirmation anywhere the operator is looking (the command returns
    nothing, so there is no generic Ok(...) toast either).
  - `teardown`'s monitor-thread-join: both the `join()` raising, and the
    previously entirely-silent case of the thread still being alive after
    the 2s join timeout elapses (a real "may still be writing to this run's
    data" condition, per the method's own docstring).
  - `set_stepper_model`: which position source feeds the log changed, with
    no report — including the silent auto-reselect path when the
    previously-selected probe is released mid-run.

Sites audited and left print-only, with the reasoning recorded in
`redpercent_system.py` itself next to each: `save_run`'s (autosave's) "no
data" guard (fires on every ordinary clean shutdown with no active run -
reporting it would be a popup on every quit), `save_log`'s "cancelled"
branch (the operator's own already-visible decision), the `sync_x/y/z`
setter guards (unreachable through any exposed UI path - the schema
declares the toggles `writable=False`, so only `toggle_sync_x()` etc. can
reach them, and those return `Refused(...)` before ever touching the
setter), `_read_dim`'s except (an explicit REDPERCENT-16 sentinel, and
reporting every occurrence at the monitor loop's rate would be exactly the
popup flood RC-8 exists to prevent), and the MONITORING STARTED/STOPPED
banners (state changes already visible live via the schema's
`disabled_when`/`enabled_when` button gating and, after REDPERCENT-13, the
`monitoring` readonly attr - not failures, which is what this finding is
about).
"""
from unittest.mock import patch

import pytest

from model.redpercent_system import RedPercentDataLog, RedPercentSystem

pytestmark = pytest.mark.redpercent


@pytest.fixture
def logged_system(tmp_path):
    sysm = RedPercentSystem()
    sysm.output_root = tmp_path / "runs"
    sysm.run_id = "C001"
    sysm.data_log = RedPercentDataLog(["X"], "tip-3", 12.5)
    sysm.data_log.add_entry(10.0, {"X": 1.0}, {"X": 0.5})
    return sysm


# -- save_log: explicit, operator-triggered save -----------------------

def test_save_log_no_data_reports_warning():
    """The operator clicked Save Log with nothing recorded; before this fix
    the only trace was a print() to a console nobody watches."""
    sysm = RedPercentSystem()
    assert sysm.data_log is None

    with patch("error_routing.ErrorRouter") as error_router:
        sysm.save_log("somefile.csv")

    error_router.report_warning.assert_called()
    title = error_router.report_warning.call_args[0][0]
    assert "Save" in title or "Nothing" in title


def test_save_log_success_reports_info(logged_system, tmp_path):
    dest = tmp_path / "out.csv"
    with patch("error_routing.ErrorRouter") as error_router:
        logged_system.save_log(str(dest))

    assert dest.exists()
    error_router.report_info.assert_called()
    title, message = error_router.report_info.call_args[0][:2]
    assert "Saved" in title
    assert str(dest) in message


# -- teardown: the monitor thread may still be running ------------------

def test_teardown_reports_when_monitor_thread_outlives_join_timeout():
    """`join(timeout=2.0)` returning without raising says nothing about
    whether the thread actually finished. Before this fix, a thread still
    alive after the timeout was entirely silent - no print, no report -
    even though the method's own docstring names this exact risk (the
    thread may still be inside a screen grab, still writing to the run's
    data log)."""
    sysm = RedPercentSystem()

    class NeverJoins:
        """Stands in for a `threading.Thread` that is alive, and stays
        alive across a bounded `join()` - this is not a hang, `join`
        returns immediately, it just reports the thread didn't finish."""
        def is_alive(self):
            return True

        def join(self, timeout=None):
            return None

    sysm._monitor_thread = NeverJoins()
    sysm._run = None

    with patch("error_routing.ErrorRouter") as error_router:
        sysm.teardown()

    error_router.report_warning.assert_called()
    titles = [c[0][0] for c in error_router.report_warning.call_args_list]
    assert any("Still Running" in t or "Thread" in t for t in titles)


# -- set_stepper_model: the position source silently changed ------------

def test_set_stepper_model_reports_info():
    sysm = RedPercentSystem()
    sysm.available_probes = {"Stepper Probe": object()}

    with patch("error_routing.ErrorRouter") as error_router:
        sysm.set_stepper_model("Stepper Probe")

    error_router.report_info.assert_called()
    title, message = error_router.report_info.call_args[0][:2]
    assert "Stepper Probe" in message
