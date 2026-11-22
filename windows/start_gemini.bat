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

REM Start server if not already running, then wait for it
"%VENV_PY%" -c "import socket; socket.create_connection(('127.0.0.1', 8300), 1)" >nul 2>&1
if %errorlevel% neq 0 (
    start "agentchattr server" "%VENV_PY%" "%CD%\run.py"
)
:wait_server
"%VENV_PY%" -c "import socket; socket.create_connection(('127.0.0.1', 8300), 1)" >nul 2>&1
if %errorlevel% neq 0 (
    timeout /t 1 /nobreak >nul
    goto :wait_server
)

"%VENV_PY%" wrapper.py gemini %*
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
