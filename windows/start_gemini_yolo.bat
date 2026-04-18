@echo off
REM agentchattr — starts server (if not running) + Gemini wrapper (auto-approve mode)
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Pre-flight: check that gemini CLI is installed
where gemini >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "gemini" was not found on PATH.
    echo   Install it first, then try again.
    echo.
    pause
    exit /b 1
)

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

python wrapper.py gemini -- --yolo
