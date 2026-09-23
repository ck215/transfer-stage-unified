"""REDPERCENT-19: Web client honours format keys from the schema.

The model declares current_red/red_change with format=".2f" in ui_schema.
app.js's pollState used to write String(val) unconditionally for readonly
numerics; it now looks the element up in the cached device schema via
_findSchemaElement and applies _formatValue when a ".Nf" format is present.

This wrapper runs the Node vm harness (js_redpercent19_format_check.js)
against the real app.js and requires a clean "OK" -- no skip-on-partial-
failure allowed. Proven to fail against 73c591a's app.js (both current_red
and red_change come back unformatted) and to pass against the fix.
"""
import subprocess
import sys
from pathlib import Path

import pytest

try:
    subprocess.run(['node', '--version'], capture_output=True, check=True)
    HAS_NODE = True
except (FileNotFoundError, subprocess.CalledProcessError):
    HAS_NODE = False


@pytest.mark.skipif(not HAS_NODE, reason='node not found')
def test_format_honored_in_pollstate():
    """pollState applies the schema's format key to every readonly numeric."""
    harness_path = Path(__file__).parent / 'js_redpercent19_format_check.js'
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
