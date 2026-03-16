@echo off
REM Mehub — starts server (if not running) + Gemini wrapper
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

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

python wrapper.py gemini
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
