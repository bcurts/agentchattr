@echo off
setlocal
REM agentchattr — starts server (if not running) + Gemini wrapper
REM Optional chat identity: --name NAME (for example: start_codex.bat --name c2)
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
call "%~dp0ensure_venv.bat"
if errorlevel 1 exit /b 1

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

REM Warn if ripgrep is missing (Gemini CLI can hang on init - upstream bug)
where rg >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Warning: ripgrep (rg) not found on PATH.
    echo   Gemini CLI can hang on "Initializing..." for several minutes.
    echo   Fix: choco install ripgrep  or  winget install BurntSushi.ripgrep
    echo   See: https://github.com/google-gemini/gemini-cli/issues/13986
    echo.
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

"%VENV_PY%" wrapper.py gemini %*
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
