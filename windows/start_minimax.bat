@echo off
setlocal
REM agentchattr — starts server (if not running) + MiniMax API agent wrapper
REM Optional chat identity: --name NAME (for example: start_codex.bat --name c2)
REM Usage: start_minimax.bat
REM Requires MINIMAX_API_KEY environment variable.
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
call "%~dp0ensure_venv.bat"
if errorlevel 1 exit /b 1

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

REM Start the configured server if needed; show and retain startup errors.
"%VENV_PY%" -c "import socket; from config_loader import load_config; socket.create_connection(('127.0.0.1', load_config()['server']['port']), 1)" >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0server_ctl.ps1" start
    if errorlevel 1 (
        echo Server startup failed. See details above.
        pause
        exit /b 1
    )
)

"%VENV_PY%" wrapper_api.py minimax %*
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
