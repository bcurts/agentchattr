@echo off
REM agentchattr — starts server (if not running) + Antigravity (agy) wrapper
cd /d "%~dp0.."

REM Pin agy's version — it self-updates and will otherwise drift out from under you
set AGY_CLI_DISABLE_AUTO_UPDATE=1

REM agy installs to %LOCALAPPDATA%\agy\bin and adds it to the *persistent* User
REM PATH — but a window opened before that install (e.g. Explorer double-click)
REM inherits a stale PATH and won't find agy. Prepend it so this always works.
if exist "%LOCALAPPDATA%\agy\bin\agy.exe" set "PATH=%LOCALAPPDATA%\agy\bin;%PATH%"

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Pre-flight: check that the agy CLI is installed
where agy >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "agy" ^(Antigravity CLI^) was not found on PATH.
    echo   Install it first, then try again.
    echo   Note: run "agy" once with no args to sign in before using it here.
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

python wrapper.py antigravity

REM Always pause on exit so the window never vanishes silently — lets you read
REM any error above whether the wrapper crashed or exited cleanly.
echo.
echo   Antigravity wrapper exited. Review the output above.
pause
