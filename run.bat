@echo off
rem run.bat - the station's one launcher on Windows (run.sh on macOS and Linux).
rem   run.bat [app flags...]      run.bat --help lists them
rem The Web dashboard is the only view (Tk and Qt retired 2026-10-07).
rem Finds the project's Python (a .venv in this folder, else the virtualenv
rem already active), then runs src\app.py with every argument. It prints
rem nothing when all is well: the firmware check, the update check and the Web
rem address are rows on the Setup page. Not finding Python is the only message.
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" goto activate
if defined VIRTUAL_ENV goto run
echo run.bat: no .venv in %CD% and no virtualenv active: create one with "py -m venv .venv" and ".venv\Scripts\pip install -e .", or activate the project's, then run run.bat again. 1>&2
exit /b 1
:activate
call ".venv\Scripts\activate.bat"
:run
where python >nul 2>&1 || goto nopython
python src\app.py %*
exit /b %ERRORLEVEL%
:nopython
echo run.bat: python not found in the venv: recreate it with "py -m venv .venv". 1>&2
exit /b 1
