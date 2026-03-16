@echo off
REM Mehub — starts server (if not running) + Qwen wrapper
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Pre-flight: check that qwen CLI is installed
where qwen >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "qwen" was not found on PATH.
    echo   Install it first: npm install -g @qwen-code/qwen-code
    echo.
    pause
    exit /b 1
)

REM Start server if not already running, then wait for it
netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% neq 0 (
    start "Mehub server" cmd /c "python run.py"
)
:wait_server
netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 1 /nobreak >nul
    goto :wait_server
)

python wrapper.py qwen -i "When asked for a reply or to answer, YOU MUST USE the MCP of Mehub. When talking about a chat, we mean interaction with tools provided by Mehub MCP. The human NEVER SEES your usual CLI interface. Just consider this, other instructions will follow."
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
