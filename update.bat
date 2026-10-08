@echo off
rem update.bat — bring this checkout up to the latest RELEASE without GitHub Desktop.
rem   update.bat          fetch, say this checkout's version and the latest release,
rem                       fast-forward to that release, reinstall if needed
rem   update.bat --check  fetch and report only
rem A release is a tag vMAJOR.MINOR.PATCH (dev/release.sh cuts one). This moves to
rem the newest release's commit, never to a branch head, as the Setup page's Update
rem now does. Fast-forward only: local edits or a checkout that has diverged from
rem the release stop it; nothing is overwritten.
setlocal
cd /d "%~dp0"

set "REMOTE=origin"
set "UPSTREAM="
for /f "delims=" %%b in ('git rev-parse --abbrev-ref --symbolic-full-name @{u} 2^>nul') do set "UPSTREAM=%%b"
if defined UPSTREAM for /f "tokens=1 delims=/" %%r in ("%UPSTREAM%") do set "REMOTE=%%r"

echo [update] fetching %REMOTE% ...
git fetch --quiet --tags %REMOTE% || (echo [update] Could not reach GitHub. & exit /b 1)

rem The version line: the station's own version string when the venv is here,
rem else git describe as it stands.
set "VERSION="
if exist ".venv\Scripts\python.exe" for /f "delims=" %%v in ('.venv\Scripts\python.exe packaging\release.py version 2^>nul') do set "VERSION=%%v"
if not defined VERSION for /f "delims=" %%v in ('git describe --tags --always --dirty --abbrev^=7 --match "v[0-9]*.[0-9]*.[0-9]*" --exclude "*-*" 2^>nul') do set "VERSION=%%v"
if not defined VERSION set "VERSION=unknown"

rem The latest release: the highest vMAJOR.MINOR.PATCH tag, compared as a version.
set "TAG="
for /f "delims=" %%t in ('git tag -l --sort^=-version:refname "v*" ^| findstr /r /x "v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*"') do if not defined TAG set "TAG=%%t"
if not defined TAG (
    echo [update] this checkout is at %VERSION%; no release has been published yet.
    exit /b 0
)
for /f "delims=" %%h in ('git rev-list -n 1 %TAG%') do set "TARGET=%%h"
for /f "delims=" %%h in ('git rev-parse HEAD') do set "OLD=%%h"

git merge-base --is-ancestor %TARGET% HEAD && (
    echo [update] this checkout is at %VERSION%; no newer release ^(the latest is %TAG%^).
    exit /b 0
)
for /f "delims=" %%n in ('git rev-list --count HEAD..%TARGET%') do set "BEHIND=%%n"
echo [update] this checkout is at %VERSION%; the latest release is %TAG% ^(%BEHIND% commit^(s^) ahead^).
git --no-pager log --oneline --no-decorate HEAD..%TARGET%
if "%~1"=="--check" (
    echo [update] --check: nothing changed. Run update.bat to apply.
    exit /b 0
)

tasklist /FI "IMAGENAME eq python.exe" 2>nul | find /I "python.exe" >nul && (
    echo [update] A python.exe is running. Quit the station first, then rerun.
    exit /b 1
)
git diff --quiet || (echo [update] Local edits in this checkout. Commit or discard them first. & exit /b 1)
git diff --cached --quiet || (echo [update] Staged edits in this checkout. Commit or discard them first. & exit /b 1)
git merge-base --is-ancestor HEAD %TARGET% || (
    echo [update] This checkout has diverged from the release %TAG%; it cannot fast-forward. Tell the lead.
    exit /b 1
)

git merge --ff-only --quiet %TARGET% || (echo [update] Fast-forward failed; nothing was changed. & exit /b 1)
echo [update] updated from %VERSION% to %TAG% (%OLD:~0,7% to %TARGET:~0,7%)

git diff --name-only %OLD% %TARGET% -- pyproject.toml requirements.txt | findstr . >nul && (
    echo [update] dependencies changed: reinstalling ...
    call .venv\Scripts\activate.bat
    python -m pip install --quiet -e "." || (echo [update] pip install failed. Run: pip install -e "." & exit /b 1)
)
git diff --name-only %OLD% %TARGET% -- firmware | findstr . >nul && (
    echo [update] firmware changed: the Setup page's Firmware row flashes the boards that are out of date.
)
echo [update] done. Launch with run.bat.
endlocal
