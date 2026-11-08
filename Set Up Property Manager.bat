@echo off
rem One-time setup for Property Manager on Windows. Double-click this file once.
rem It needs Python 3.12 (from python.org) and an internet connection.
rem Running it again is safe; it just re-installs the same packages.

rem Work from the folder this file is in.
cd /d "%~dp0"

echo Setting up Property Manager. This takes a few minutes and needs the internet.
echo.

rem Find Python 3.12 or newer. The "py" launcher comes with Python from python.org.
rem Try 3.12 first, then the newest Python the launcher knows, then "python" on the PATH.
set "PYTHON="
py -3.12 -c "import sys" >nul 2>&1 && set "PYTHON=py -3.12"
if not defined PYTHON (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "PYTHON=py -3"
)
if not defined PYTHON (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "PYTHON=python"
)
if not defined PYTHON (
    echo Python 3.12 isn't installed on this computer.
    echo Your browser will now open the Python download page. Install Python 3.12,
    echo then double-click "Set Up Property Manager.bat" again.
    start "" "https://www.python.org/downloads/"
    echo.
    pause
    exit /b 1
)

rem Create a private copy of Python for this app (.venv) and install its packages there.
%PYTHON% -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

rem Make the Property Manager icon on the Desktop and in the Start menu.
".venv\Scripts\python.exe" install_launcher.py
if errorlevel 1 goto failed

echo.
echo Setup finished. To open the app, double-click its icon on the Desktop.
echo.
pause
exit /b 0

:failed
echo.
echo Setup didn't finish. Please check the internet connection and try again.
echo If it keeps failing, send a photo of this window to whoever helps you with the app.
echo.
pause
exit /b 1
