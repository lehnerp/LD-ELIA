@echo off
rem ==========================================================================
rem  Entra Local Sign-in Dashboard - one-time setup / repair
rem  Finds Python 3.10+, creates .venv, installs packages, prepares the
rem  local SQLite case file (data.sqlite3) and runs a quick self-test.
rem  Safe to run again at any time: your case data is never deleted.
rem  Usage: setup.bat            (asks to start the dashboard at the end)
rem         setup.bat --no-start (used by start.bat)
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Entra dashboard - setup
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "PY="

echo.
echo  Entra Local Sign-in Dashboard - setup
echo  -------------------------------------
echo  Folder: %~dp0
echo.

rem ---- 1. Find Python 3.10 or newer -----------------------------------------
echo [1/5] Looking for Python 3.10 or newer...
call :try_python py -3
if not defined PY call :try_python python
if not defined PY call :try_python python3
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY call :try_python "%%~fD\python.exe"
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do if not defined PY call :try_python "%%~fD\python.exe"

if not defined PY (
    echo.
    echo  Python 3.10+ was not found.
    where winget >nul 2>&1
    if errorlevel 1 goto :no_python
    choice /c YN /m "  Install Python 3.12 for the current user now with winget"
    if errorlevel 2 goto :no_python
    winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
    for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY call :try_python "%%~fD\python.exe"
    if not defined PY call :try_python py -3
    if not defined PY goto :no_python
)
<nul set /p "=      Found: "
%PY% --version

rem ---- 2. Virtual environment -----------------------------------------------
echo [2/5] Preparing virtual environment .venv ...
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo       Existing .venv is broken ^(folder moved or Python removed^). Recreating...
        rmdir /s /q ".venv"
    )
)
if not exist "%VENV_PY%" (
    %PY% -m venv .venv
    if errorlevel 1 goto :fail_venv
)

rem ---- 3. Packages ----------------------------------------------------------
echo [3/5] Installing packages ^(Flask, waitress, python-dateutil^)...
"%VENV_PY%" -m pip install --upgrade pip --disable-pip-version-check -q >nul 2>&1
"%VENV_PY%" -m pip install -r requirements.txt --disable-pip-version-check -q
if errorlevel 1 goto :fail_pip
"%VENV_PY%" -m pip install -r requirements-geoip.txt --disable-pip-version-check -q >nul 2>&1
if errorlevel 1 (
    echo       Optional GeoIP package not installed - the dashboard works without it.
) else (
    echo       Optional GeoIP support installed ^(needs your own MMDB file, see README^).
)

rem ---- 4. Local SQLite case file --------------------------------------------
echo [4/5] Preparing local SQLite case file...
"%VENV_PY%" app.py --init
if errorlevel 1 goto :fail_db

rem ---- 5. Self-test -----------------------------------------------------------
echo [5/5] Running self-test...
"%VENV_PY%" -m unittest discover -s tests >"%TEMP%\entra-dashboard-tests.log" 2>&1
if errorlevel 1 (
    echo       WARNING: some self-tests failed. Details: %TEMP%\entra-dashboard-tests.log
) else (
    echo       All self-tests passed.
)

echo.
echo  Setup complete. Start the dashboard any time with start.bat
echo  Your investigation is stored in data.sqlite3 in this folder;
echo  start.bat makes a backup in the backups folder on every launch.
echo.
if /i "%~1"=="--no-start" exit /b 0
choice /c YN /m "  Start the dashboard now"
if errorlevel 2 exit /b 0
endlocal
call "%~dp0start.bat"
exit /b %errorlevel%

rem ---------------------------------------------------------------------------
:try_python
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=%*"
exit /b 0

:no_python
echo.
echo  Please install Python 3.10 or newer from https://www.python.org/downloads/
echo  ^(tick "Add python.exe to PATH" in the installer^), then run setup.bat again.
goto :end_fail

:fail_venv
echo.
echo  Could not create the virtual environment in .venv
echo  Check that this folder is writable and not inside a ZIP file.
goto :end_fail

:fail_pip
echo.
echo  Package installation failed. Check your internet connection or proxy,
echo  then run setup.bat again.
goto :end_fail

:fail_db
echo.
echo  The SQLite case file could not be prepared. Is data.sqlite3 open in another program?
goto :end_fail

:end_fail
echo.
if /i not "%~1"=="--no-start" pause
exit /b 1
