@echo off
REM agentchattr — starts server (if not running) + API agent wrapper
REM Usage: start_api_agent.bat <agent_name>
REM Example: start_api_agent.bat qwen
cd /d "%~dp0.."

set AGENT_NAME=%~1
if "%AGENT_NAME%"=="" (
    echo.
    echo   agentchattr — API Agent Launcher
    echo   ---------------------------------
    echo   Enter the agent name from your config.local.toml
    echo   Example: qwen, mistral, llama, deepseek
    echo.
    set /p AGENT_NAME="  Agent name: "
)
if "%AGENT_NAME%"=="" (
    echo   Error: No agent name provided.
    exit /b 1
)

REM Auto-create venv and install deps on first run
if not exist ".venv" (
    python -m venv .venv
    .venv\Scripts\pip install -q -r requirements.txt >nul 2>nul
)
call .venv\Scripts\activate.bat

REM Start server if not already running, then wait for it
netstat -ano | findstr :8300 | findstr LISTENING >nul 2>&1
if %errorlevel% neq 0 (
    start "agentchattr server" cmd /k "python run.py"
)
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

python wrapper_api.py %AGENT_NAME%
