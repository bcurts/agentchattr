@echo off
REM agentchattr — starts server (if not running) + OrcaRouter API agent wrapper
REM Usage: start_orca.bat
REM Requires ORCAROUTER_API_KEY environment variable.
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Check API key
if "%ORCAROUTER_API_KEY%"=="" (
    echo.
    echo   Error: ORCAROUTER_API_KEY environment variable is not set.
    echo   Get an API key at https://www.orcarouter.ai
    echo   Then: set ORCAROUTER_API_KEY=your-key-here
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

python wrapper_api.py orca
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
