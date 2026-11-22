@echo off
REM Redirection means this is not an interactive console; callers may also opt out.
if "%AGENTCHATTR_NO_PAUSE%"=="1" exit /b 0
powershell -NoProfile -Command "if ([Console]::IsInputRedirected) { exit 1 } else { exit 0 }"
if not errorlevel 1 pause
exit /b 0
