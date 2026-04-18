@echo off
REM agentchattr — starts server (if not running) + Copilot wrapper
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
    start "agentchattr server" cmd /k "python run.py"
)
set /a _wait=0
:wait_server
netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% equ 0 goto server_ready
set /a _wait+=1
if %_wait% geq 30 (
    echo ERROR: Server did not start within 30 seconds. Check the server window for errors.
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_server
:server_ready

python wrapper.py copilot
