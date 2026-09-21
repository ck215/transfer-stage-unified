"""REDPERCENT-19: Web client should honour format keys from the schema.

app.js pollState currently does String(val) for readonly numerics.
The model declares current_red/red_change with format=".2f".
This test harness proves pollState reads and applies the format key.
"""
import subprocess
import sys
import pytest
from pathlib import Path

# Check if node is available
try:
    subprocess.run(['node', '--version'], capture_output=True, check=True)
    HAS_NODE = True
except (FileNotFoundError, subprocess.CalledProcessError):
    HAS_NODE = False


@pytest.mark.skipif(not HAS_NODE, reason='node not found')
def test_format_honored_in_pollstate():
    """Test that pollState applies format key from schema elements."""
    harness_path = Path(__file__).parent / 'js_redpercent19_format_check.js'
    app_js_path = Path(__file__).parent.parent.parent / 'src/views/web/static/js/app.js'

    if not harness_path.exists():
        pytest.skip('harness not found')
    if not app_js_path.exists():
        pytest.skip('app.js not found')

    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '--co', '-q'],  # Just list, don't run
        capture_output=True,
        cwd=str(Path(__file__).parent.parent)
    )

    # Run the actual harness
    result = subprocess.run(
        ['node', str(harness_path), str(app_js_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Parse output
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0:
        pytest.fail(f'Harness failed: {stdout or stderr}')

    if stdout != 'OK':
        pytest.fail(f'Harness did not return OK: {stdout}')
