"""REDPERCENT-17 (Web renderer half): dim selection and real error messages
in the plot dialog.

`/api/plot` used to pick the first N header dims in file order with no way
for the operator to choose one, and app.js showed a single fixed
"Failed to generate plot" toast for every server-side failure -- including
the blank-PNG case a 2D/3D request with a missing dimension used to
produce (see tests/core/test_redpercent17_plot_blank_figure_message.py for
the renderer half of that same finding). This wrapper runs the Node vm
harness that drives app.js's plot dialog handlers end-to-end: choosing a
file populates the dim pickers from its own header, Generate sends the
operator's picks, and a server failure's actual message reaches the toast.

Proven to fail against 73c591a's app.js (no onchange handler on the file
input at all, so the dim pickers never populate; no dim1/dim2/dim3 in the
POST body; and a fixed toast string regardless of the server's message).
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
def test_plot_dialog_dim_pickers_populate_and_post():
    harness_path = Path(__file__).parent / 'js_redpercent17_plot_dims_check.js'
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
