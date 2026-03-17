@echo off
REM Mehub — starts the server only
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo   Mehub is already running on http://127.0.0.1:8300
    echo   Stop the existing process first if you want to restart it.
    echo.
    pause
    exit /b 0
)

where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Error: npm is required to build the Mehub web UI.
    echo.
    pause
    exit /b 1
)

if not exist "web\node_modules\.bin\tsc" (
    echo Installing web UI dependencies...
    pushd web
    if exist "package-lock.json" (
        call npm ci
    ) else (
        call npm install
    )
    if %errorlevel% neq 0 (
        popd
        echo.
        echo   Error: failed to install web UI dependencies.
        echo.
        pause
        exit /b 1
    )
    popd
)

if not exist "web\node_modules\.bin\tsc" (
    echo.
    echo   Error: failed to install web UI dependencies.
    echo.
    pause
    exit /b 1
)

echo Building Mehub web UI...
pushd web
call npm run build
if %errorlevel% neq 0 (
    popd
    echo.
    echo   Error: failed to build the Mehub web UI.
    echo.
    pause
    exit /b 1
)
popd

python run.py
echo.
echo === Server exited with code %ERRORLEVEL% ===
pause
