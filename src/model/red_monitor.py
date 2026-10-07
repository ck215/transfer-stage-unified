"""TRANSITIONAL (RG-3, 2026-10-07): Red Percent is RGB analysis, now
`model.rgb_analysis.RgbAnalysis` (NAME "RGB Analysis").

This alias exists only so the files that were outside the rename's write
set during the parallel round keep importing (tests/test_transfer_map.py,
tests/test_setup_registry.py). It registers nothing: `RedMonitor` IS
`RgbAnalysis`, the one class Setup registers. Delete this file together
with those imports' one-line updates (handoff/fix-rgb.md, FOR THE LEAD).
"""
from model.rgb_analysis import (CHANNEL_COLUMNS, MonitorRun,  # noqa: F401
                                RgbAnalysis, RunLog)

RedMonitor = RgbAnalysis
