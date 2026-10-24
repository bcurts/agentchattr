@echo off
REM agentchattr - starts server (if not running) + CodeBuddy wrapper
REM Usage: start_codebuddy.bat
REM Requires the codebuddy CLI on PATH. First launch prompts interactive login.
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Pre-flight: check that codebuddy CLI is installed
where codebuddy >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "codebuddy" was not found on PATH.
    echo   Install it from https://www.codebuddy.ai/cli then try again.
    echo.
    pause
    exit /b 1
)

REM Start server if not already running, then wait for it
python -c "import socket; socket.create_connection(('127.0.0.1', 8300), 1)" >nul 2>&1
if %errorlevel% neq 0 (
    start "agentchattr server" cmd /c "python run.py"
)
:wait_server
python -c "import socket; socket.create_connection(('127.0.0.1', 8300), 1)" >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 1 /nobreak >nul
    goto :wait_server
)

python wrapper.py codebuddy
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
