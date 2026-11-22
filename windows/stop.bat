@echo off
REM agentchattr - stops the server only (agent wrappers keep running and reconnect)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0server_ctl.ps1" stop
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo.
    echo Server control failed with exit code %RESULT%. See details above.
    if /i not "%~1"=="--no-pause" call "%~dp0pause_if_interactive.bat"
)
exit /b %RESULT%
