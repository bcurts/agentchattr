@echo off
setlocal
REM agentchattr — starts server (if not running) + API agent wrapper
REM Usage: start_api_agent.bat <agent_name> [--name CHAT_NAME]
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
    pause
    exit /b 1
)

REM Auto-create venv and install deps on first run
call "%~dp0ensure_venv.bat"
if errorlevel 1 exit /b 1

REM Start the configured server if needed; show and retain startup errors.
"%VENV_PY%" -c "import socket; from config_loader import load_config; socket.create_connection(('127.0.0.1', load_config()['server']['port']), 1)" >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0server_ctl.ps1" start
    if errorlevel 1 (
        echo Server startup failed. See details above.
        pause
        exit /b 1
    )
)

"%VENV_PY%" wrapper_api.py "%AGENT_NAME%" %2 %3
pause
