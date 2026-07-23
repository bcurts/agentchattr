@echo off
REM agentchattr — starts server (if not running) + Ezra relay (Resonant bridge)
REM No CLI is spawned: this bridges @ezra mentions to the RUNNING Resonant
REM service. Start Resonant first (Start-Karma-Assistant) or this will exit.
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Start server if not already running, then wait for it
netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% neq 0 (
    start "agentchattr server" cmd /c "python run.py"
)
:wait_server
netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 1 /nobreak >nul
    goto :wait_server
)

python wrapper_ezra.py
if %errorlevel% neq 0 (
    echo.
    echo   Relay exited unexpectedly. Check the output above.
    pause
)
