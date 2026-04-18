@echo off
REM Launch the Electron desktop shell for agentchattr.
REM The server must be running (or start it with start.bat / any agent launcher).
cd /d "%~dp0\..\electron"
if not exist "node_modules\" (
  echo Installing Electron dependencies (first-run, ~1 min)...
  call npm install
  if errorlevel 1 (
    echo npm install failed. Make sure Node.js is installed: https://nodejs.org
    pause
    exit /b 1
  )
)
call npm start
