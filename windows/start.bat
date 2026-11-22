@echo off
setlocal
REM agentchattr - starts the server in the background and returns once it is listening.
REM Use stop.bat / restart.bat to stop or restart it. For a visible console, run: .venv\Scripts\python.exe run.py
cd /d "%~dp0.."
call "%~dp0ensure_venv.bat"
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0server_ctl.ps1" start
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo.
    echo Server control failed with exit code %RESULT%. See details above.
    if /i not "%~1"=="--no-pause" call "%~dp0pause_if_interactive.bat"
)
exit /b %RESULT%
