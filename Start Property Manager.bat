@echo off
rem Double-click this file to start Property Manager on Windows.
rem Keep the window that opens while you use the app. Closing it stops the app.

rem Work from the folder this file is in.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Property Manager isn't set up on this computer yet.
    echo Please double-click "Set Up Property Manager.bat" first.
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" run.py

rem If the app stopped because of a problem, keep the window open so the message can be read.
if errorlevel 1 (
    echo.
    pause
    exit /b 1
)
