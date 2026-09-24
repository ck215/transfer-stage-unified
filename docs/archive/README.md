# Archive

Pages moved here on 2026-09-23 (docs prune after the relayout). Every one
describes the old MVC repair tree, now at `legacy/src/`, as if it were the
app. The app is the rebuild in `src/`; start at `docs/rebuild/STATUS.md`.
Nothing here is kept current. The paths inside these pages (`src/...`,
`tests/...`) mean today's `legacy/src/...` and `legacy/tests/...`.

Each file keeps its original path under `docs/archive/`, and `git log
--follow` shows its history.

| File | What it was | Why archived |
|---|---|---|
| `architecture/README.md` | Index and reading order for the architecture docs of the repair branch (2026-09-18/19). | It indexes the pages below and frames the old `src/` as the live app. The inputs it pointed at (`audit/`, `root-causes.md`, `safety-pattern.md`) are still in `docs/architecture/`. |
| `architecture/bootstrap-and-entrypoint.md` | Startup flow of `src/app.py` / `src/app_bootstrap.py`: `SetupWindow`, `build_models`, the three `run_*_app` paths. | The rebuild's entry point is `src/app.py` → `Controller` + `Setup`. None of the classes described exist in it. |
| `architecture/controllers.md` | `ControllerPoller` (gamepad) and the `serial` transport of the old tree. | Replaced by `src/devices/gamepad.py` and `src/devices/serial_port.py`, with a different API. |
| `architecture/error-routing.md` | The old `error_routing` event bus and per-frontend wiring. | Replaced by `src/events.py` (one `EventLog`). |
| `architecture/known-issues.md` | Pre-audit issue log (2026-09-18), later mapped onto the root causes. | Superseded by `audit/` and the ledger even on the old branch; describes only old code. |
| `architecture/libs-and-web.md` | `src/lib/smc100.py`, `toupcam.py`, and the old web adapter. | SMC100 is now `src/devices/smc100.py`; the Web view was rewritten (`src/views/web/`) and is now the candidate primary frontend, where this page calls it deprioritised. |
| `architecture/models.md` | `ManagedModel`, `BaseProbe`, `RedPercentSystem`, `RotatorSystem`, `TemperatureSystem`, `SystemManager`. | Replaced by `src/model/base.py` and `src/model/{probe,heater,rotator,red_monitor}.py`; `SystemManager` became `Controller`. |
| `architecture/ownership-and-lifecycle.md` | Ownership graph, `SystemManager` lifecycle primitives, dock close/reopen. | Close = destruct / reopen = construct is now a ruling implemented by `Controller.remove/reopen`; hide/show was purged. |
| `architecture/views.md` | PySide6 and Tkinter view class hierarchies of the old tree. | The views were rewritten (`src/views/{tk,qt}.py`, `src/views/web/`). |
| `implementation/plan.md` | The 17-stage repair plan (S0–S16) and its commit protocol. | The stages ended when the rebuild replaced the repair. What is still open from S16 (bench) is Tier B of `docs/rebuild/BUGFIX_PLAN.md`. |
| `implementation/testing.md` | Test policy for the old suite: markers, `known_bad`/`order_dependent` quarantine, per-stage gates. | Describes `legacy/tests/` only. Comments in `legacy/tests/conftest.py`, `legacy/tests/pytest.ini` and `legacy/tests/architecture/test_invariants.py` still cite it at its old path; read it here. |

Kept in place, not archived, because open work reads them (each has a
banner): `docs/implementation/progress.md` (the ledger; `carry.json` cites
its IDs), `docs/implementation/bench-checklist.md` (the bench procedure
behind Tier B), `docs/architecture/audit/*.md`,
`docs/architecture/root-causes.md`, `docs/architecture/safety-pattern.md`.
`docs/implementation/gen_ledger.py` stays beside `progress.md`, which it
rewrites.
