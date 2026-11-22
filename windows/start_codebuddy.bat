@echo off
setlocal
REM agentchattr - starts server (if not running) + CodeBuddy wrapper
REM Optional chat identity: --name NAME (for example: start_codex.bat --name c2)
REM Usage: start_codebuddy.bat
REM Requires the codebuddy CLI on PATH. First launch prompts interactive login.
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
call "%~dp0ensure_venv.bat"
if errorlevel 1 exit /b 1

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

"%VENV_PY%" wrapper.py codebuddy %*
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
