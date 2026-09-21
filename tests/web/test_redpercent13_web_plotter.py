"""REDPERCENT-13 - the Web plotter's remaining two defects.

The exact-attribute-match half (mixing `current_red`/`red_change` into one
series via a substring match on the attribute name) was already fixed and is
pinned by `test_web_7_plotter.py` (WEB-7). Reading `pollState` at HEAD
confirms it: the check is `attr === 'current_red'`, not
`attr.toLowerCase().includes('red')`.

Two defects from the same audit entry were still live:

  1. `pollState` pushed a plotter sample on every poll, regardless of
     whether a run was active - the chart drew a flat line while stopped.
  2. The plotter modal's "Reset" button only ever touched
     `this.baselineRed`/`this.plotterData.deltaRed` (pure client state); it
     never called the model's own `reset_baseline`, so the model's
     `baseline_red` (what the schema's "Red Change %" readout is actually
     computed from) disagreed with the chart's delta from the moment of the
     first click onward.

Fixed by: gating `pushPlotterSample` on a new `monitoring` readonly schema
attr (REDPERCENT-13's model half, `tests/core/test_redpercent13_19_schema.py`
pins that it is published), and having both Reset click handlers dispatch
`reset_baseline` to the model before touching their own local state.

`app.js` has no test runner in this repo. This is verified by execution, via
a Node harness that loads the real source into a `vm` sandbox and drives
`TransferStageApp.prototype.pollState`/`initEventListeners` directly - the
same technique `test_web_13_stop_monitoring_offers_save.py` uses.
"""
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
HERE = os.path.dirname(__file__)
APP_JS = os.path.join(HERE, "..", "..", "src", "views", "web", "static", "js", "app.js")
HARNESS = os.path.join(HERE, "js_redpercent13_plotter_check.js")


@pytest.mark.skipif(NODE is None, reason="node is not available in this environment")
def test_pollstate_gates_and_reset_dispatches_to_model():
    """One harness, two assertions bundled together (see the .js file's own
    comment): `pollState` must only sample the plotter while `monitoring` is
    true, and the modal Reset button must dispatch `reset_baseline` to the
    model rather than only resetting client-side state."""
    result = subprocess.run(
        [NODE, HARNESS, APP_JS],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout!r} stderr={result.stderr!r}")
    assert "OK" in result.stdout
