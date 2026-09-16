# tests/in_progress/

Agent-generated and work-in-progress test modules that have **not** been
validated or promoted to the main test suite.

## Status

These files were produced by automated agents during the `web-view` feature
development session (2026-09-16). They cover model, HAL, view, QA, and probe
logic but **require review** before they can be trusted:

- Some import symbols that don't exist yet (e.g. `STATE_NOT_REFERENCED` — now
  fixed in `src/lib/smc100.py`).
- Some contain Qt fixture usage that crashes headless runners without
  `QT_QPA_PLATFORM=offscreen`.
- None have been independently validated by a human or a second-pass QA agent.

## Files

| File | Intended coverage |
|---|---|
| `test_controller_round1.py` | Controller layer round-1 sweep |
| `test_hal_round1.py` | HAL / SMC100 round-1 |
| `test_hal_round2.py` | HAL / SMC100 round-2 (imports `STATE_NOT_REFERENCED`) |
| `test_model_round1.py` | Model layer round-1 |
| `test_model_round2.py` | Model layer round-2 |
| `test_probes.py` | Probe model unit tests |
| `test_qa_round1.py` | QA sweep round-1 |
| `test_redpercent.py` | RedPercent system |
| `test_temperature.py` | Temperature subsystem |
| `test_view_round1.py` | View / Qt round-1 (requires display or offscreen) |

## Next steps for the agent picking this up

1. Run each file individually under `QT_QPA_PLATFORM=offscreen` to identify
   remaining import or assertion failures.
2. Fix or delete tests that duplicate coverage already in `tests/core/` or
   `tests/edge_cases/`.
3. Promote green, non-duplicate tests into the appropriate validated suite
   directory and delete the originals here.
4. Once this directory is empty, remove it and the `collect_ignore_glob` entry
   in `pytest.ini`.
