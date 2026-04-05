# Agent Launcher Panel — Design Spec

## Overview

Add an in-browser agent launcher panel to agentchattr so users can launch, stop, monitor, and configure AI coding agents (Claude, Codex, Gemini, etc.) directly from the web UI — without needing separate terminal windows or batch files.

## Goals

1. Launch any configured agent from the UI with one click
2. Configure CLI flags (e.g. `--dangerously-skip-permissions`, `--yolo`, `--worktree`) per launch via preset toggles and free-text
3. Set working directory globally or per-agent
4. Launch multiple instances of the same agent (e.g. two Claudes with different flags)
5. View last ~100 lines of agent output (read-only log tail)
6. Stop running agents from the UI
7. Add/edit/remove agent definitions from the UI (no config.toml editing required)
8. On server restart, offer to relaunch agents from the previous session

## Architecture

### Current flow (external launchers)

```
User double-clicks start_claude.bat
  -> bat creates venv, starts server if needed
  -> bat runs: python wrapper.py claude
  -> wrapper.py registers with server, spawns agent subprocess, runs heartbeat
  -> agent is online
```

### New flow (server-managed)

```
User clicks "Launch" in browser UI
  -> browser POSTs to /api/agents/<name>/launch with flags, cwd, extra_args
  -> server spawns wrapper.py as a managed subprocess
  -> wrapper.py registers, spawns agent, runs heartbeat (same as before)
  -> server captures wrapper stdout/stderr into a ring buffer (last 100 lines)
  -> server streams log lines to browser via WebSocket
  -> user can stop via POST /api/agents/<name>/stop (server kills the wrapper process)
```

The key insight: the server becomes the process manager for wrappers. The wrappers themselves are unchanged — they still register, heartbeat, and inject keystrokes exactly as they do today. The server just owns their lifecycle instead of a batch file.

### Component breakdown

#### 1. Process Manager (`process_manager.py` — new file)

Responsibilities:
- Spawn `wrapper.py` subprocesses with configured flags, cwd, and env
- Capture stdout/stderr into a per-agent ring buffer (100 lines)
- Track process state: starting, running, crashed, stopped
- Kill processes on stop request
- Persist launch configs to `data/launch_state.json` for session restore
- Provide log lines to the WebSocket broadcast system

State per managed agent:
```python
{
    "name": "claude",           # agent base name
    "pid": 12345,               # wrapper process PID
    "state": "running",         # starting | running | crashed | stopped
    "flags": ["--dangerously-skip-permissions"],
    "extra_args": [],
    "cwd": "C:\\AI\\MyProject",
    "started_at": 1743868800.0,
    "log_buffer": ["line1", "line2", ...],  # ring buffer, max 100
}
```

Key design decisions:
- One ProcessManager instance, owned by the server (created in `configure()`)
- Wrapper subprocesses use `subprocess.Popen` with `stdout=PIPE, stderr=STDOUT`
- A reader thread per process drains stdout and appends to the ring buffer
- On process exit, state transitions to "crashed" (with exit code) or "stopped" (if user-initiated)
- No automatic restart from the server side — the user relaunches from the UI

#### 2. REST API endpoints (in `app.py`)

All endpoints require session token (cookie or header).

**Launch:**
```
POST /api/agents/<base>/launch
Body: {
    "flags": ["--dangerously-skip-permissions"],
    "extra_args": ["--model", "opus"],
    "cwd": "C:\\AI\\MyProject",       // optional, falls back to default
    "instance_label": null             // optional, for multi-instance naming
}
Response: { "ok": true, "name": "claude", "pid": 12345 }
```

**Stop:**
```
POST /api/agents/<name>/stop
Response: { "ok": true }
```
Note: `<base>` in launch refers to the agent type (e.g. "claude"); `<name>` in stop refers to the specific instance (e.g. "claude-2"). Stop is a hard kill (SIGTERM then SIGKILL after 5s) — wrapper deregisters on exit, so presence updates immediately.

**Logs:**
```
GET /api/agents/<name>/logs
Response: { "lines": ["line1", "line2", ...] }
```

**List managed agents:**
```
GET /api/agents/managed
Response: [{ "name": "claude", "state": "running", "pid": 12345, "flags": [...], "cwd": "...", "started_at": ... }, ...]
```

**Agent definitions (CRUD):**
```
GET    /api/agent-definitions          -> list all agent definitions
POST   /api/agent-definitions          -> add new agent definition
PUT    /api/agent-definitions/<name>   -> update agent definition
DELETE /api/agent-definitions/<name>   -> remove agent definition
```

Agent definitions are stored in `data/agent_definitions.json`. On startup, definitions from `config.toml` are loaded as defaults; user-added definitions are merged in. User-added definitions take precedence over config.toml for the same name.

#### 3. WebSocket events (additions to existing WS protocol)

**Server -> Client:**
```json
{"type": "agent_process", "data": {"name": "claude", "state": "running", "pid": 12345, "flags": [...], "cwd": "..."}}
{"type": "agent_log", "data": {"name": "claude", "line": "Registered as claude (slot 1)"}}
{"type": "agent_definitions", "data": [{"name": "claude", "command": "claude", "color": "#da7756", ...}]}
{"type": "session_restore", "data": [{"name": "claude", "flags": [...], "cwd": "...", "base": "claude"}]}
```

#### 4. Launch State Persistence (`data/launch_state.json`)

On every launch/stop, the process manager writes the current set of running agents and their launch configs:

```json
[
    {
        "base": "claude",
        "flags": ["--dangerously-skip-permissions"],
        "extra_args": [],
        "cwd": "C:\\AI\\MyProject",
        "instance_label": null
    },
    {
        "base": "codex",
        "flags": ["--dangerously-bypass-approvals-and-sandbox"],
        "extra_args": [],
        "cwd": "C:\\AI\\MyProject",
        "instance_label": null
    }
]
```

On server restart:
1. Server reads `data/launch_state.json`
2. Sends `session_restore` event to the first WebSocket client that connects
3. Browser shows the restore banner with checkboxes
4. User selects which agents to relaunch
5. Browser POSTs `/api/agents/<base>/launch` for each selected agent
6. Server clears the restore state after the user acts (relaunch or dismiss)

#### 5. Preset Flags (per agent type)

Known flag presets are defined in the server and sent to the UI. Each preset has a label and the actual CLI flag:

```python
AGENT_FLAG_PRESETS = {
    "claude": [
        {"label": "Skip permissions", "flag": "--dangerously-skip-permissions"},
        {"label": "Worktree", "flag": "--worktree"},
    ],
    "codex": [
        {"label": "Bypass approvals", "flag": "--dangerously-bypass-approvals-and-sandbox"},
    ],
    "gemini": [
        {"label": "Yolo", "flag": "--yolo"},
        {"label": "Sandbox", "flag": "--sandbox"},
    ],
    "qwen": [
        {"label": "Yolo", "flag": "--yolo"},
    ],
}
```

The UI renders these as toggle buttons. The free-text "Extra Arguments" field handles anything not in the presets.

#### 6. Frontend (`static/launcher.js` — new file)

A new JS module following the same pattern as `jobs.js` and `sessions.js`:
- Reads from `window`: `ws`, `activeChannel`, `agentConfig`
- Subscribes to Hub for `agent_process`, `agent_log`, `agent_definitions`, `session_restore` events
- Renders the launcher panel (slide-out from a new header button, like Jobs/Rules)

Panel structure:
- **Header**: "Agents" title + "+ Add Agent" button
- **Agent cards**: one per defined agent, sorted running-first then alphabetical
  - Running: coloured dot (glowing), name, meta (command, cwd, uptime), flags, Stop/Logs/Launch Another buttons
  - Stopped: dimmed dot, name, meta, Launch button
  - Crashed: red dot, name, exit info, Relaunch/Logs buttons
- **Logs drawer**: collapsible per-agent, shows last 100 lines, auto-scrolls, monospace
- **Launch config**: expands below a stopped agent's card when "Launch" is clicked
  - Working directory input (pre-filled with default)
  - Flag preset toggles (agent-specific)
  - Extra arguments free-text
  - "Launch [Agent]" button
- **"Launch Another" button** on running agents: opens the same launch config form but for a new instance of that agent type
- **Add Agent form**: name, command, label, colour picker (swatches), MCP mode dropdown. "Save Agent" persists to `data/agent_definitions.json`
- **Edit/Remove**: available on stopped, user-defined agents

**Session restore banner**: rendered at the top of the chat area (not inside the panel) when `session_restore` event arrives. Checkbox list of previous agents with their flags and cwd. "Relaunch Selected" and "Dismiss" buttons. Disappears after action.

#### 7. Default Working Directory

A new setting in `data/settings.json`:
```json
{
    "default_cwd": "C:\\AI\\MyProject"
}
```

Editable from Settings panel. Used as the pre-filled value in all launch config forms. Per-agent cwd overrides this.

#### 8. Multi-Instance Support

When clicking "Launch Another" on a running agent (e.g. Claude):
1. UI opens the launch config form with the same defaults
2. User can change flags, cwd, or add an instance label
3. POST to `/api/agents/claude/launch` — the server spawns another wrapper
4. Wrapper registers with the server, gets assigned "claude-2" (existing multi-instance logic)
5. The launcher panel shows both instances as separate cards, grouped under the same agent type header

Instance naming follows existing registry logic: first instance is "claude", second is "claude-2", etc. If instance_label is provided, the wrapper passes it as the `--label` argument.

Card grouping: when multiple instances of the same type are running, they appear as a group with a subtle type header (e.g. "Claude (2 instances)"). Each instance card shows its assigned name ("claude", "claude-2"), its own flags, cwd, and independent Stop/Logs buttons. The "Launch Another" button appears once at the group level.

## Security Considerations

- All launcher API endpoints require session token (enforced by existing middleware)
- `cwd` is validated to be an existing directory before launch
- Agent command must match a defined agent (no arbitrary command execution)
- Subprocess spawning uses argument lists (no `shell=True`)
- Flag presets are server-defined; free-text args are passed through to the wrapper (which passes them to the CLI)
- Log buffer is in-memory only (not persisted to disk)

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `process_manager.py` | Create | Process lifecycle, log capture, state persistence |
| `static/launcher.js` | Create | Frontend panel, cards, forms, log viewer |
| `app.py` | Modify | Add REST endpoints, WS events, wire up ProcessManager |
| `run.py` | Modify | Create ProcessManager, load launch state, handle restore |
| `static/chat.js` | Modify | Add header button, wire up launcher panel toggle |
| `static/index.html` | Modify | Add `<script src="launcher.js">`, panel container div |
| `static/styles.css` | Modify | Launcher panel styles (or inline in launcher.js) |
| `data/agent_definitions.json` | Auto-created | User-defined agent configs |
| `data/launch_state.json` | Auto-created | Previous session state for restore |

## Out of Scope

- Full embedded terminal (xterm.js) — read-only log tail only
- Agent-to-agent process dependencies (e.g. "start Codex after Claude")
- Remote agent launching (always localhost)
- Modifying wrapper.py or wrapper_windows.py — they work as-is
