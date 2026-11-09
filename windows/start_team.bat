@echo off
setlocal
cd /d "%~dp0.."

REM Check mode never installs dependencies or starts server/agent sessions.
if /i "%~1"=="--check" goto check

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
echo Checking agentchattr dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
goto run

:check
if not exist ".venv\Scripts\python.exe" (
    echo Missing Python environment. Run start_team.bat without --check to set it up.
    exit /b 1
)

:run
".venv\Scripts\python.exe" "windows\start_team.py" %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Team startup failed. Review the error above.
if /i not "%~1"=="--check" pause
exit /b 1
