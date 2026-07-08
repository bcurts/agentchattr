@echo off
REM agentchattr — starts server (if not running) + Kimi wrapper (yolo mode)
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Pre-flight: check that kimi CLI is installed
where kimi >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "kimi" was not found on PATH.
    echo   Install it first, then try again.
    echo.
    pause
    exit /b 1
)

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

REM Kimi Code 0.6+ no longer accepts --mcp-config-file.
REM wrapper.py writes .kimi-code/mcp.json and passes per-instance auth via env.
set "KIMI_ARGS=--yolo"
python wrapper.py kimi %KIMI_ARGS%
set "EXIT_CODE=%errorlevel%"
if %EXIT_CODE% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
    exit /b %EXIT_CODE%
)
