@echo off
REM agentchattr — starts server (if not running) + MiniMax API agent wrapper
REM Usage: start_minimax.bat
REM Requires MINIMAX_API_KEY environment variable.
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Check API key
if "%MINIMAX_API_KEY%"=="" (
    echo.
    echo   Error: MINIMAX_API_KEY environment variable is not set.
    echo   Get an API key at https://platform.minimax.io
    echo   Then: set MINIMAX_API_KEY=your-key-here
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

python wrapper_api.py minimax
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
