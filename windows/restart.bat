@echo off
REM agentchattr - stops the server, waits for it to exit, restarts it and waits until it is listening
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0server_ctl.ps1" restart
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo.
    echo Server control failed with exit code %RESULT%. See details above.
    if /i not "%~1"=="--no-pause" call "%~dp0pause_if_interactive.bat"
)
exit /b %RESULT%
