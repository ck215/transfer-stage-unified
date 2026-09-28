@echo off
rem update.bat — bring this checkout up to date with GitHub without GitHub Desktop.
rem   update.bat          fetch, show what is coming, fast-forward, reinstall if needed
rem   update.bat --check  fetch and report only
rem Fast-forward only: local edits or local commits stop it; nothing is overwritten.
setlocal
cd /d "%~dp0"

for /f "delims=" %%b in ('git rev-parse --abbrev-ref --symbolic-full-name @{u} 2^>nul') do set UPSTREAM=%%b
if not defined UPSTREAM (
    echo [update] This checkout tracks no remote branch. Set one: git branch -u origin/^<branch^>
    exit /b 1
)
for /f "tokens=1 delims=/" %%r in ("%UPSTREAM%") do set REMOTE=%%r

echo [update] fetching %UPSTREAM% ...
git fetch --quiet %REMOTE% || (echo [update] Could not reach GitHub. & exit /b 1)

for /f "delims=" %%h in ('git rev-parse HEAD') do set OLD=%%h
for /f "delims=" %%h in ('git rev-parse %UPSTREAM%') do set NEW=%%h
for /f "delims=" %%n in ('git rev-list --count HEAD..%UPSTREAM%') do set BEHIND=%%n
for /f "delims=" %%n in ('git rev-list --count %UPSTREAM%..HEAD') do set AHEAD=%%n

if "%BEHIND%"=="0" (
    echo [update] up to date.
    exit /b 0
)
echo [update] %BEHIND% new commit(s) on %UPSTREAM%:
git --no-pager log --oneline --no-decorate HEAD..%UPSTREAM%
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
if not "%AHEAD%"=="0" (
    echo [update] This tree has %AHEAD% local commit(s) GitHub does not; it cannot fast-forward. Tell the lead.
    exit /b 1
)

git merge --ff-only --quiet %UPSTREAM% || (echo [update] Fast-forward failed; nothing was changed. & exit /b 1)
echo [update] updated to %NEW:~0,7%

git diff --name-only %OLD% %NEW% -- pyproject.toml requirements.txt | findstr . >nul && (
    echo [update] dependencies changed: reinstalling ...
    call .venv\Scripts\activate.bat
    python -m pip install --quiet -e ".[qt]" || (echo [update] pip install failed. Run: pip install -e ".[qt]" & exit /b 1)
)
git diff --name-only %OLD% %NEW% -- firmware | findstr . >nul && (
    echo [update] firmware changed: run_swap flashes the boards that are out of date on the next launch.
)
echo [update] done. Launch with run.bat.
endlocal
