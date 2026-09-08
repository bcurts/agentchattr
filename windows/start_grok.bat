@echo off
REM agentchattr - starts server (if not running) + Grok Build wrapper
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM grok installs to %USERPROFILE%\.grok\bin. Explorer double-click often
REM inherits a stale PATH; prepend after activate.bat, which rewrites PATH.
if exist "%USERPROFILE%\.grok\bin\grok.exe" set "PATH=%USERPROFILE%\.grok\bin;%PATH%"

REM Pre-flight: check that grok CLI is installed
where grok >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "grok" was not found on PATH.
    echo   Install it first ^(PowerShell^): irm https://x.ai/cli/install.ps1 ^| iex
    echo   Official install dir: %USERPROFILE%\.grok\bin
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

python wrapper.py grok
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
