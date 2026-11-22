@echo off
setlocal
REM agentchattr — starts server (if not running) + Kilo wrapper
REM Usage: start_kilo.bat [provider/model] [--name CHAT_NAME]
REM   e.g. start_kilo.bat anthropic/claude-sonnet-4-20250514
REM   Omit the model to use Kilo's configured default.
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
call "%~dp0ensure_venv.bat"
if errorlevel 1 exit /b 1

REM Pre-flight: check that kilo CLI is installed
where kilo >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "kilo" was not found on PATH.
    echo   Install it first, then try again.
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

if /i "%~1"=="--name" (
    "%VENV_PY%" wrapper.py kilo --name "%~2"
) else if "%~1"=="" (
    "%VENV_PY%" wrapper.py kilo
) else (
    "%VENV_PY%" wrapper.py kilo %2 %3 -- -m "%~1"
)
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
