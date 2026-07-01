# Desktop Launcher MVP

This note covers the Windows desktop launcher MVP smoke path for agentchattr.
The process supervisor remains intentionally small: it can start and stop only
processes it created, while externally started `.bat` processes are displayed as
external and are not killed by the launcher.

## Install

From the repository root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-desktop.txt
```

`requirements.txt` is still required for the FastAPI server. The desktop
requirements file contains the native desktop shell dependency plus the Windows
packaging tools: PySide6, PyInstaller, and Pillow.

## Run

```powershell
python desktop_launcher.py
```

For the web-hosted control panel fallback, start the server and open the
launcher route:

```powershell
python run.py
```

Then open:

```text
http://localhost:8300/launcher
```

## Build Windows EXE

The desktop launcher is packaged as a PyInstaller `onedir` app. Build it from
the repository root after installing both requirements files:

```powershell
python build_desktop_exe.py
```

The output is:

```text
dist/agentchattr/agentchattr.exe
```

Keep the whole `dist/agentchattr/` folder together. Do not move only the exe:
the launcher reads `config.toml`, `static/`, and other runtime resources from
the same folder. If you need local overrides, place `config.local.toml` beside
`agentchattr.exe`.

## MVP Behavior

- The launcher reads agent templates from `config.toml` and `config.local.toml`.
- The server can be started from the launcher only when it is not already
  listening on the configured host and port.
- Stop and restart are enabled only for server and agent processes that were
  started by the launcher.
- Externally started agents, including agents launched with `windows/start_*.bat`,
  are visible but are not stoppable from the launcher.
- Agent instance names are assigned by `wrapper.py` and `registry.py`; the
  launcher does not invent names.
- Logs are captured only for launcher-owned subprocesses.
- Yolo/bypass mode is mapped in the backend. The UI should send only
  `mode: "normal"` or `mode: "yolo"`.

## MVP Limits

- No full interactive PTY.
- No tray icon or auto-start-on-login behavior.
- No profile manager beyond the existing config files.
- No log search, filtering, or log-level parsing.
- No attempt to terminate external processes.
- No replacement for the existing `.bat` launchers.

## Windows Smoke Checklist

Use PowerShell from the repository root unless noted otherwise.

1. Install dependencies with `requirements.txt` and `requirements-desktop.txt`.
2. Run `python desktop_launcher.py`, or build and double-click
   `dist/agentchattr/agentchattr.exe`. For the web fallback, run `python run.py`
   and open `http://localhost:8300/launcher`.
3. Confirm the launcher shows the configured host, port, MCP ports, and agent
   templates from config.
4. With no server running, click Start Server and verify the server enters a
   running state.
5. Click Open Chat and verify `http://localhost:8300` loads.
6. Start one agent in normal mode and verify a launcher-owned process appears.
7. Verify server stdout/stderr lines appear in the log view; agent CLI windows
   are hosted by Windows Terminal.
8. Start a second instance of the same agent and verify the registry-assigned
   instance name appears when the wrapper registers.
9. Stop the launcher-owned agent and verify its state changes to stopped.
10. Start an agent with `windows/start_*.bat`, refresh the launcher, and verify
    it is shown as external with stop/restart disabled.
11. Try yolo mode for an unsupported agent and verify the API/UI shows a clear
    validation error.
12. Occupy the configured server port with another process and verify the
    launcher reports the server as external or blocked instead of killing it.
13. Temporarily remove an agent CLI from `PATH` and verify a clear start error
    is surfaced.
14. Confirm existing `.bat` launchers still start agents and register them in
    chat.

## Unit Test Focus

The MVP tests should stay fast and avoid starting real agent CLIs. Covered
surfaces:

- supervisor status serialization for server, templates, and managed processes
- log capture and recent-log slicing
- external process stop rejection
- shared action/button rules for launcher-owned versus external processes

Run the focused test file:

```powershell
python -m unittest tests.test_launcher_supervisor
```
