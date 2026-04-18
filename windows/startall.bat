@echo off
REM agentchattr — starts server + all bot wrappers (copilot, claude, codex, gemini)
cd /d "%~dp0.."

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Start server if not already running, then wait for it
netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% neq 0 (
    start "agentchattr server" cmd /k "call .venv\Scripts\activate.bat && python run.py"
)

REM Wait up to 30 seconds for server to come up
set /a _wait=0
:wait_server
netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% equ 0 goto server_ready
set /a _wait+=1
if %_wait% geq 30 (
    echo ERROR: Server did not start within 30 seconds. Check the server window for errors.
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto :wait_server

:server_ready
echo Server is running. Launching all bots...

start "agentchattr copilot" cmd /k "call .venv\Scripts\activate.bat && python wrapper.py copilot -- --yolo"
start "agentchattr claude"  cmd /k "call .venv\Scripts\activate.bat && python wrapper.py claude --dangerously-skip-permissions"
start "agentchattr codex"   cmd /k "call .venv\Scripts\activate.bat && python wrapper.py codex -- --dangerously-bypass-approvals-and-sandbox"
start "agentchattr gemini"  cmd /k "call .venv\Scripts\activate.bat && python wrapper.py gemini -- --yolo"

REM Launch Electron desktop window (first run will auto-install deps)
start "agentchattr desktop" cmd /k "cd /d %~dp0..\electron && (if not exist node_modules npm install) && npm start"

echo All bots and desktop window launched in separate windows.
