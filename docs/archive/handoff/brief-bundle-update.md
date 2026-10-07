# Brief: bundles that update themselves, and the builds that feed them (branch `rb-bundle`, worktree `../rb-bundle`)

Base: `345763f` or later on `mvc-refactor`. Python:
`/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python`.
Read `.claude/agents/worktree-fixer.md` first (its rules bind you), then
`docs/rebuild/PACKAGING_PLAN.md` (P3–P8; P5 is yours), `packaging/station.spec`,
`packaging/smoke.sh`, `packaging/smoke.ps1`, `packaging/entry_*.py`,
`pyproject.toml`, `src/controller/updater.py` (the git path and its
statuses; the `bundle` status), `src/controller/setup.py` (the Update row,
`apply_update`, `restart_station`, the "Update Ready" / "Restart Needed"
prompts), `src/app.py` (`restart_process`), `tests/test_updater.py`,
`tests/test_setup.py`, `tests/test_packaging.py`.

## Owner rulings (2026-09-28)

The repo stays private. Installed bundles read release information from
GitHub. Only authorised machines pull updates. Updates on a source checkout
stay as they are (git fast-forward).

## Design (the lead's; deviations go in the handoff with reasons)

B1. **A version the bundle knows.** The build writes `VERSION` beside the
    launchers: `<tag>\n<sha>\n<built at ISO>`. `Updater` reads it when
    frozen (`station_version` shows the tag and date); a source checkout
    keeps its git version. `pyproject.toml`'s version follows the tag at
    build time (the workflow patches it from the tag; the file in git
    stays `0.1.0`-style and is not the source of truth).
B2. **P5, the builds.** `.github/workflows/package.yml`: on a tag `v*`
    (and by hand, `workflow_dispatch`), a matrix of `macos-14`,
    `macos-13`, `windows-latest`, `ubuntu-22.04`; Python 3.13 pinned
    (`actions/setup-python`, the python.org build so Tk ships); `pip
    install -e .[qt,dev]`; `pyinstaller packaging/station.spec`; write
    `VERSION`; run the smoke script where it can run headless (Web and
    Tk; Qt offscreen); zip `dist/station` as
    `station-<os>-<arch>.zip`; upload as a release asset of the tag's
    GitHub Release (`softprops/action-gh-release` or `gh release
    upload`). Nothing signs (P7 is the owner's).
B3. **The bundle's update path.** In `updater.py`, when frozen:
    `check()` asks `https://api.github.com/repos/<owner>/<repo>/releases/latest`
    with a token from `~/transfer-stage-runs/github_token` (one line;
    absent -> status `unauthorised`: "This machine has no update token.
    Ask the lead for one and put it in <path>."), compares its tag to
    `VERSION`, and reports `behind` with the release's name and body's
    first line, `up_to_date`, `offline`, `unauthorised` (401/404), or
    `error`. `apply()` downloads this platform's asset
    (`station-<os>-<arch>.zip`, matched from `platform`/`sys` without a
    platform branch anywhere the operator sees) to a temp dir beside the
    install, verifies size (and sha256 when the release body lists one),
    unpacks to `<install>.next`, then swaps: `<install>` ->
    `<install>.previous`, `<install>.next` -> `<install>`; the Setup line
    reads "Updated to <tag>. Restart the station to run it." and the
    Restart prompt follows as on a checkout. A failed swap restores
    `.previous` and reports. All network through `urllib` with timeouts;
    injectable `fetch=` for tests; the token never appears in the log.
    The repo owner/name come from `packaging/release.json` written at
    build time (owner, repo, asset naming), never hard-coded in `src/`.
B4. **The Update row copy** covers both worlds without saying which:
    "Up to date (v1.2.0)", "v1.3.0 is ready: <first line>. Update now,
    then restart." A source checkout's copy is unchanged.
B5. **Tests first.** `tests/test_updater.py` (additive): a fake GitHub
    (`fetch=`) answering latest-release JSON and an asset; behind /
    up-to-date / unauthorised / offline; apply swaps directories and
    restores on failure; the token is read from the file and never
    logged (assert on the log). `tests/test_packaging.py`: the workflow
    file exists, names the four runners and the spec, uploads the four
    asset names; `VERSION` is read. Nothing in tests touches the network.
B6. **Docs you own:** `docs/rebuild/PACKAGING_PLAN.md` (P5 done, P8's
    install text: download the zip for your OS, unzip, run the launcher;
    the token file for updates), `README.md` "Install" section (P8).

## Write set (exclusive)

- NEW `.github/workflows/package.yml`, `packaging/**` (spec, smoke, NEW
  `release.json` template, hooks), `src/controller/updater.py`,
  `src/controller/setup.py` (the copy for the two statuses only),
  `pyproject.toml` (a comment on the version source only)
- `tests/test_updater.py`, `tests/test_packaging.py`, `tests/test_setup.py`
  (additive)
- `docs/rebuild/PACKAGING_PLAN.md`, `README.md` (the Install section)

Not yours: `src/app.py`, `src/model/**`, the views, `docs/rebuild/STATUS.md`.

## Gates before you commit

- `STATION_NO_WINDOWS=1 <PY> -m pytest tests -q -p no:cacheprovider -m "not qt"`
  to a file under your scratch dir, exit code unpiped: all green.
- Golden: 78.
- `pyinstaller` is NOT run here (13 minutes under Seafile); the workflow
  is validated with `actionlint` if available, else by a YAML parse and
  the packaging tests. Nothing on screen; no network calls from tests.

## Handoff

`handoff/fix-bundle-update.md`. Commit on `rb-bundle`; never push.
