"""WEB-24: app.js gates controls from the schema's enabled_when/disabled_when.

pollState's interlock block used to re-derive "mode" from raw polled flags
(auton_flag/manual_flag/system_enabled) and find controls by
ctrl.innerText.includes('Full Stop'/'Enable'/'Power Down') -- none of which
are per-device controls any schema still renders. This wrapper runs the
Node vm harness that drives pollState through a fake device schema with
gated and ungated controls, including one deliberately labeled "Full Stop"
whose label is renamed mid-test, and requires a clean OK: every control's
disabled state must come from its own schema element's enabled_when/
disabled_when, never from its label text.

Proven to fail against 73c591a's app.js (the "Full Stop"-labeled button
stays enabled during autonomous mode regardless of its own disabled_when,
and the ungated Clear FULL STOP button gets force-disabled it should not
touch) and to pass against the fix.
"""
import subprocess
from pathlib import Path

import pytest

try:
    subprocess.run(['node', '--version'], capture_output=True, check=True)
    HAS_NODE = True
except (FileNotFoundError, subprocess.CalledProcessError):
    HAS_NODE = False


@pytest.mark.skipif(not HAS_NODE, reason='node not found')
def test_pollstate_gates_from_schema_not_labels():
    harness_path = Path(__file__).parent / 'js_web24_schema_gate_check.js'
    app_js_path = Path(__file__).parent.parent.parent / 'src/views/web/static/js/app.js'

    assert harness_path.exists(), 'harness not found'
    assert app_js_path.exists(), 'app.js not found'

    result = subprocess.run(
        ['node', str(harness_path), str(app_js_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    assert result.returncode == 0 and stdout == 'OK', (
        f'harness did not report a clean OK: stdout={stdout!r} stderr={stderr!r}'
    )
