@echo off
REM agentchattr — starts server (if not running) + Qwen wrapper
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Qwen Code is installed by npm, but npm's global bin may not be on PATH.
REM Add common Windows npm/Node locations for this process so wrapper.py inherits them.
set "QWEN_NPM_BIN=%APPDATA%\npm"
if exist "%QWEN_NPM_BIN%\qwen.cmd" (
    set "PATH=%QWEN_NPM_BIN%;%PATH%"
)

set "QWEN_WORKBUDDY_BIN="
for /f "delims=" %%D in ('dir /b /ad /o-d "%USERPROFILE%\.workbuddy\binaries\node\versions" 2^>nul') do (
    if not defined QWEN_WORKBUDDY_BIN if exist "%USERPROFILE%\.workbuddy\binaries\node\versions\%%D\qwen.cmd" set "QWEN_WORKBUDDY_BIN=%USERPROFILE%\.workbuddy\binaries\node\versions\%%D"
)
if defined QWEN_WORKBUDDY_BIN (
    set "PATH=%QWEN_WORKBUDDY_BIN%;%PATH%"
)

set "QWEN_SERVBAY_BIN=C:\ServBay\packages\node\current"
if exist "%QWEN_SERVBAY_BIN%\qwen.cmd" (
    set "PATH=%QWEN_SERVBAY_BIN%;%PATH%"
)

REM Pre-flight: check that qwen CLI is installed
where qwen >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "qwen" was not found on PATH.
    echo   Checked: %QWEN_NPM_BIN%
    if defined QWEN_WORKBUDDY_BIN echo   Checked: %QWEN_WORKBUDDY_BIN%
    echo   Checked: %QWEN_SERVBAY_BIN%
    echo   Install it first: npm install -g @qwen-code/qwen-code
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

python wrapper.py qwen -i "When asked for a reply or to answer, YOU MUST USE the mcp of agentchattr. When talking about a chat, we are speaking about interaction with tools provided by mcp of agentchattr. The human NEVER SEES your usual CLI interface. Just consider this, other instructions will follow."
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
