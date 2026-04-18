@echo off
REM agentchattr — stops server + all bot wrappers (copilot, claude, codex, gemini)

echo Stopping all agentchattr processes...

REM Graceful shutdown first (allows Python finally blocks / deregistration to run)
taskkill /FI "WINDOWTITLE eq agentchattr copilot" >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr claude"  >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr codex"   >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr gemini"  >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr server"  >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr desktop" >nul 2>&1

timeout /t 3 /nobreak >nul

REM Force-kill anything still running
taskkill /FI "WINDOWTITLE eq agentchattr copilot" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr claude"  /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr codex"   /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr gemini"  /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr server"  /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq agentchattr desktop" /F >nul 2>&1
REM Kill any lingering Electron processes tied to this app (matches productName "agentchattr")
taskkill /FI "WINDOWTITLE eq agentchattr" /F >nul 2>&1

echo All agentchattr processes stopped.
