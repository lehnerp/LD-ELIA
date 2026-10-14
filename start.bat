@echo off
rem ==========================================================================
rem  Entra Local Sign-in Dashboard - start
rem  Runs setup automatically the first time, backs up the SQLite case file,
rem  starts the server on http://127.0.0.1:8585 and opens your browser.
rem  Optional: set DASHBOARD_PORT=8686 before running to use another port.
rem  Optional: set GEOIP_DB=C:\path\GeoLite2-City.mmdb for offline GeoIP.
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Entra dashboard
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo First run - preparing the dashboard...
    call "%~dp0setup.bat" --no-start
    if errorlevel 1 goto :failed
)
"%VENV_PY%" -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo Packages missing - repairing installation...
    call "%~dp0setup.bat" --no-start
    if errorlevel 1 goto :failed
)

echo Backing up the case file...
"%VENV_PY%" app.py --backup

echo.
"%VENV_PY%" app.py --open
set "CODE=%errorlevel%"
if "%CODE%"=="3" (
    echo.
    timeout /t 6 >nul
    exit /b 0
)
if not "%CODE%"=="0" (
    echo.
    echo The dashboard stopped with an error ^(code %CODE%^).
    goto :failed
)
exit /b 0

:failed
echo.
pause
exit /b 1
