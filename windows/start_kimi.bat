@echo off
setlocal
REM agentchattr — starts server (if not running) + Kimi wrapper
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
call "%~dp0ensure_venv.bat"
if errorlevel 1 exit /b 1

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

"%VENV_PY%" wrapper.py kimi
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
