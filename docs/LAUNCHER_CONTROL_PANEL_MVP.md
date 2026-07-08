# Launcher Control Panel MVP

## Purpose

Build a real agentchattr control panel from the v3.1.2 mockup while keeping the
first implementation narrow. The panel should make the Windows workflow easier:
open chat, start/stop the server, start/stop agents, and view basic logs in one
place.

This document is the implementation baseline for the current job:

- Frontend: kimi-1
- Backend: kimi-2
- Testing: Qwen
- Planning, architecture, review: Codex

## MVP Scope

Must ship:

- Control panel entry point.
- Open Chat action for the configured server URL.
- Server status, start, stop, and restart.
- Agent template list sourced from `config.toml` / `config.local.toml`.
- Agent start, stop, and restart.
- Add Agent drawer matching v3.1.2 semantics:
  - agent type
  - normal or yolo mode
  - role, default none
  - custom role text when custom is selected
  - working directory
  - advanced settings
  - auto-start preference
- Instance names assigned by `wrapper.py` / `registry.py`, not by the UI.
- Basic stdout/stderr log tabs for processes started by the launcher.
- Clear error states for missing CLI, port conflict, wrapper failure, and health
  check failure.

Out of scope for MVP:

- Full interactive PTY.
- System tray behavior.
- Complex profiles.
- Log search or log level parsing.
- Killing externally started processes.
- Replacing existing `.bat` launchers.
- Typing indicator changes.

## Architecture Decision

Implement this inside the existing `agentchattr` project first. Reuse the
current Python/FastAPI stack and static frontend structure. Avoid adding a heavy
desktop framework until the process model and user flow are proven.

The launcher layer must stay thin:

- Read existing config with `config_loader.load_config`.
- Start `python run.py` for the server when needed.
- Start `python wrapper.py <base> ...extra_args` for agents.
- Keep handles only for processes started by the launcher.
- Detect externally running server/agents, but do not stop or kill them in MVP.
- Capture stdout/stderr for launcher-owned processes.

## Backend Modules

Recommended new files:

- `launcher.py`
  - Process supervisor and state model.
  - Knows how to start/stop/restart server and agent subprocesses.
  - Owns in-memory process registry for launcher-owned processes.
- `launcher_routes.py`
  - FastAPI endpoints and websocket for launcher features.
  - Calls `launcher.py`; does not directly manage subprocesses.
- `static/launcher.html`
  - Control panel page.
- `static/launcher.js`
  - Frontend behavior and API calls.
- `static/launcher.css`
  - Styles adapted from v3.1.2 mockup.

Register routes from `run.py` or `app.py` in the same style as existing
application routes. Keep the change small and explicit.

## Data Model

Use simple models first.

```text
AgentTemplate
- base
- label
- command
- cwd
- color
- normal_args
- yolo_args
- supports_yolo

ManagedProcess
- key
- kind: server | agent
- base
- assigned_name
- pid
- status
- started_by_launcher
- started_at
- last_error

LogEvent
- process_key
- stream: stdout | stderr
- text
- timestamp
```

`assigned_name` may be empty at process start. Fill it once the wrapper
registers or once the backend can resolve the active registry instance.

## Yolo Mode

The frontend sends only `mode: "normal"` or `mode: "yolo"`.

The backend maps mode to real command arguments per agent template. Do not
hard-code `--yolo` in the frontend.

Initial mapping must be verified before use:

- Codex: pass through to CLI as `-- --dangerously-bypass-approvals-and-sandbox`.
- Claude: pass through to CLI as `-- --dangerously-skip-permissions`.
- Gemini: likely `-- --yolo` or existing launcher equivalent.
- Qwen: likely `-- --yolo` or existing launcher equivalent.
- Kimi: unknown; treat yolo as unsupported until verified.

If an agent does not support yolo mode, the API should return a clear validation
error and the UI should show it.

## API Contract

Minimal endpoints:

```text
GET  /api/launcher/status
GET  /api/launcher/agents
POST /api/launcher/server/start
POST /api/launcher/server/stop
POST /api/launcher/server/restart
POST /api/launcher/agents/{base}/start
POST /api/launcher/processes/{key}/stop
POST /api/launcher/processes/{key}/restart
GET  /api/launcher/logs/{key}
WS   /ws/launcher/events
```

Start agent request:

```json
{
  "base": "kimi",
  "mode": "normal",
  "role": null,
  "custom_role": null,
  "cwd": "D:\\kimicode",
  "auto_start": false
}
```

Start agent response:

```json
{
  "process_key": "agent:kimi:1234",
  "base": "kimi",
  "assigned_name": null,
  "status": "starting",
  "started_by_launcher": true
}
```

## Frontend Requirements

The frontend must not infer or invent:

- instance names
- process ownership
- yolo command arguments
- agent health

The frontend should render backend state and provide actions only when the
backend says they are allowed.

Pages for MVP:

- Overview: server status, Open Chat, high-level agent counts.
- Agents: templates and running instances, start/stop/restart actions.
- Terminal: log tabs for launcher-owned processes.
- Settings: lightweight placeholders only unless needed by MVP.

## Testing Plan

Qwen should verify these flows on Windows:

1. Server not running -> start server -> Open Chat works.
2. Server already running externally -> panel shows external running state.
3. Start one agent -> process appears -> logs stream.
4. Start second instance of same agent -> registry-assigned name appears.
5. Stop launcher-owned agent -> process exits and state updates.
6. Stop external process action is unavailable.
7. Missing CLI shows a clear error.
8. Port conflict shows a clear error.
9. Yolo unsupported agent returns validation error.
10. Existing `.bat` launchers still work.

## Review Gates

Codex review checkpoints:

1. API and module boundaries before broad implementation.
2. Server process management before agent management expands.
3. Yolo mapping before exposing it widely in UI.
4. Stop/restart behavior before Qwen full test pass.
5. Final MVP review before considering PTY, tray, profiles, or richer logs.

