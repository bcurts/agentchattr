@echo off
REM agentchattr — starts server (if not running) + Codex wrapper
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Codex Desktop/CLI may install outside the regular user PATH after updates.
REM Add the known install locations for this process so wrapper.py inherits them.
set "CODEX_LOCAL_BIN=%LOCALAPPDATA%\OpenAI\Codex\bin"
set "CODEX_LOCAL_VERSIONED_BIN="
if exist "%CODEX_LOCAL_BIN%\codex.exe" (
    set "PATH=%CODEX_LOCAL_BIN%;%PATH%"
)
for /f "delims=" %%D in ('dir /b /ad /o-d "%CODEX_LOCAL_BIN%" 2^>nul') do (
    if not defined CODEX_LOCAL_VERSIONED_BIN if exist "%CODEX_LOCAL_BIN%\%%D\codex.exe" set "CODEX_LOCAL_VERSIONED_BIN=%CODEX_LOCAL_BIN%\%%D"
)
if defined CODEX_LOCAL_VERSIONED_BIN (
    set "PATH=%CODEX_LOCAL_VERSIONED_BIN%;%PATH%"
)

where codex >nul 2>&1
if %errorlevel% equ 0 goto :codex_path_ready

set "CODEX_WINDOWSAPP_BIN="
for /f "delims=" %%D in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue).InstallLocation" 2^>nul') do (
    if not defined CODEX_WINDOWSAPP_BIN set "CODEX_WINDOWSAPP_BIN=%%D\app\resources"
)
if not defined CODEX_WINDOWSAPP_BIN goto :codex_path_ready
if not exist "%CODEX_WINDOWSAPP_BIN%\codex.exe" goto :codex_path_ready
"%CODEX_WINDOWSAPP_BIN%\codex.exe" --version >nul 2>&1
if %errorlevel% equ 0 set "PATH=%CODEX_WINDOWSAPP_BIN%;%PATH%"

:codex_path_ready

REM Pre-flight: check that codex CLI is installed
where codex >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: "codex" was not found on PATH.
    echo   Checked: %CODEX_LOCAL_BIN%
    if defined CODEX_LOCAL_VERSIONED_BIN echo   Checked: %CODEX_LOCAL_VERSIONED_BIN%
    if defined CODEX_WINDOWSAPP_BIN echo   Checked: %CODEX_WINDOWSAPP_BIN%
    echo   Install or repair Codex Desktop/CLI, then try again.
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

python wrapper.py codex
if %errorlevel% neq 0 (
    echo.
    echo   Agent exited unexpectedly. Check the output above.
    pause
)
