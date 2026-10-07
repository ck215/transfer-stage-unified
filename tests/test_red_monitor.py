"""TRANSITIONAL (RG-3, 2026-10-07): the RGB analysis tests are
`tests/test_rgb_analysis.py` (was this file) and
`tests/test_rgb_analysis_transients.py`.

Kept only because tests/test_transfer_map.py, outside the rename's write set
during the parallel round, imports its fake screens from here. No test lives
here. Delete this file with that import's one-line update
(handoff/fix-rgb.md, FOR THE LEAD).
"""
from test_rgb_analysis import desktop_screen, fake_screen  # noqa: F401
