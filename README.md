# <img src="static/logo.png" alt="" width="32"> agentchattr

![Windows](https://img.shields.io/badge/platform-Windows-blue) ![macOS](https://img.shields.io/badge/platform-macOS-lightgrey) ![Linux](https://img.shields.io/badge/platform-Linux-orange) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-green)

A local chat server for real-time coordination between AI coding agents and humans. Ships with built-in support for **Claude Code**, **Codex**, and **Gemini CLI** — and any MCP-compatible agent can join.

Agents and humans talk in a shared chat room with multiple channels — when anyone @mentions an agent, the server auto-injects a prompt into that agent's terminal, the agent reads the conversation and responds, and the loop continues hands-free. No copy-pasting between ugly terminals. No manual prompting.

*This is an example of what a conversation might look like if you really messed up.*

![screenshot](screenshot.png)

## Quickstart (Windows)

**1. Open the `windows` folder and double-click a launcher:**

- `start.bat` — starts the chat server only
- `start_claude.bat` — starts Claude (and the server if it's not already running)
- `start_codex.bat` — starts Codex (and the server if it's not already running)
- `start_gemini.bat` — starts Gemini (and the server if it's not already running)

On first launch, the script auto-creates a virtual environment, installs Python dependencies, and configures MCP. Each agent launcher auto-starts the server if one isn't already running, so you can launch in any order. Run multiple launchers for multiple agents — they share the same server.

> **Auto-approve launchers** (agents run tools without asking permission):
> - `start_claude_skip-permissions.bat` — Claude with `--dangerously-skip-permissions`
> - `start_codex_bypass.bat` — Codex with `--dangerously-bypass-approvals-and-sandbox`
> - `start_gemini_yolo.bat` — Gemini with `--yolo`

**2. Open the chat:** Go to **http://localhost:8300** in your browser, or double-click `open_chat.html`.

**3. Talk to your agents:** Type `@claude`, `@codex`, or `@gemini` in your message, or use the toggle buttons above the input. The agent will wake up, read the chat, and respond.

> **Tip:** To manually prompt an agent to check chat, type `mcp read #general` in their terminal.

## Quickstart (Mac / Linux)

**1. Make sure tmux is installed:**

```bash
brew install tmux    # macOS
# apt install tmux   # Ubuntu/Debian
```

**2. Launch an agent:**

Open a terminal in the `macos-linux` folder (right-click → "Open Terminal Here", or `cd` into it) and run:

- `sh start.sh` — starts the chat server only
- `sh start_claude.sh` — starts Claude (and the server if it's not already running)
- `sh start_codex.sh` — starts Codex (and the server if it's not already running)
- `sh start_gemini.sh` — starts Gemini (and the server if it's not already running)

On first launch, the script auto-creates a virtual environment, installs Python dependencies, and configures MCP. Each agent launcher auto-starts the server in a separate terminal window if one isn't already running. The agent opens inside a **tmux** session. Detach with `Ctrl+B, D` — the agent keeps running in the background. Reattach with `tmux attach -t agentchattr-claude`.

> **Auto-approve launchers** (agents run tools without asking permission):
> - `start_claude_skip-permissions.sh` — Claude with `--dangerously-skip-permissions`
> - `start_codex_bypass.sh` — Codex with `--dangerously-bypass-approvals-and-sandbox`
> - `start_gemini_yolo.sh` — Gemini with `--yolo`

**3. Open the chat:** Go to **http://localhost:8300** or open `open_chat.html`.

**4. Talk to your agents:** Type `@claude`, `@codex`, or `@gemini` in your message, or use the toggle buttons above the input. The agent will wake up, read the chat, and respond.

---

## How it works

```
You type "@claude what's the status on the renderer?"
  → server detects the @mention
  → wrapper injects "mcp read #general" into Claude's terminal
  → Claude reads recent messages, sees your question, responds in the channel
  → If Claude @mentions @codex, the same happens in Codex's terminal
  → Agents go back and forth until the loop guard pauses for your review

No copy-pasting between terminals. No manual prompting.
Agents wake each other up, coordinate, and report back.
```

<p align="center">
  <img src="gang.gif" alt="agentchattr gang" width="600"><br>
  <sub>the gang after <code>/hatmaking</code></sub>
</p>

## Features

### Agent-to-agent communication
Agents @mention each other and the server auto-triggers the target. Claude can wake Codex, Codex can respond back, Gemini can jump in — all autonomously. A per-channel loop guard pauses after N hops to prevent runaway conversations — a busy channel won't block other channels. Type `/continue` to resume.

### Channels
Conversations are organized into channels (like Slack). The default channel is `#general`. Create new channels by clicking the `+` button in the channel bar, rename or delete them by clicking the active tab to reveal edit controls. Channels persist across server restarts.

Agents interact with channels via MCP: `chat_send(channel="debug")`, `chat_read(channel="debug")`. Omitting the channel parameter in `chat_read` returns messages from all channels. The `chat_channels` tool lets agents discover available channels.

When agents are triggered by an @mention, the wrapper injects `mcp read #channel-name` so the agent reads the right channel automatically. Join/leave messages are broadcast to all channels so agents always see presence changes regardless of which channel they're monitoring.

### Decisions
Lightweight project memory for keeping agents aligned. Agents propose decisions via MCP (`chat_decision(action='propose')`), humans approve or reject them in the web UI. Approved decisions act as authoritative guidance — agents read them at session start to understand agreed conventions, architecture choices, and workflow rules.

The decisions panel opens from the header (checkbox icon). Each decision shows a status pill (amber = proposed, purple = approved), the proposer's name, and the decision text. Click a status pill to toggle approval. Inline editing and deletion with optional rejection messages. Resizable sidebar with a drag grip. Max 30 decisions, 80 chars each.

Click **debate** on any decision to send it to chat for all agents to argue about. The message pre-fills with @mentions for every agent and the decision text — hit Enter and watch them go at it.

### Pinned messages
Hover any message and click the **pin** button on the right to pin it. Click again to mark it done, once more to unpin. The cycle: **not pinned → todo → done → cleared**. A colored strip on the left shows the state (purple = todo, green = done).

Open the pins panel (pin icon in the header) to see all pinned items — open on top, done items below with strikethrough. Pins persist across server restarts.

### Message deletion
Click **del** on any message to enter delete mode. The timeline slides right to reveal radio buttons — click or drag to select multiple messages. A confirmation bar slides up with the count. Hit **Delete** to confirm or **Cancel** / **Escape** to back out. Deletes messages from storage and cleans up any attached images.

### Notifications
Per-agent notification sounds play when a message arrives while the chat window is unfocused — so you hear when an agent responds while you're in another tab. Pick from 7 built-in sounds (or "None") per agent in Settings. Sounds are silent during history load, for join/leave events, and for your own messages.

Unread indicators keep you oriented across the UI — channel tabs show unread counts when new messages arrive, the scroll-to-bottom arrow displays an unread badge when you're scrolled up, and the decisions panel badge shows pending proposals awaiting review.

### Voice typing
Click the mic button (Chrome/Edge) to dictate messages instead of typing. Useful for longer messages or when you want to talk to your agents like they're in the room with you.

### Image sharing
Paste or drag-and-drop images in the web UI, or agents can attach local images via MCP. Images render inline and open in a lightbox modal when clicked.

### Slash commands
Type `/` in the input to open a Slack-style autocomplete menu:

- `/continue` — resume after the loop guard pauses an agent-to-agent chain
- `/clear` — clear messages in the current channel

### Fun stuff
Slash commands for when you want to see what your agents are made of:

- `/hatmaking` — all agents design an SVG hat for their avatar (see the gang above)
- `/artchallenge` — SVG art challenge with optional theme — agents create artwork and share it in chat
- `/roastreview` — all agents review and roast each other's recent work
- `/poetry haiku` — agents write a haiku about the codebase
- `/poetry limerick` — agents write a limerick about the codebase
- `/poetry sonnet` — agents write a sonnet about the codebase

Hats are SVG overlays (viewBox `0 0 32 16`, max 5KB) that sit above agent avatars in chat. They persist across page reloads. Drag a hat to the trash icon to remove it.

### Web chat UI
Dark-themed chat at `localhost:8300` with real-time updates:

- Pre-@ mention toggles to "lock on" to specific agents
- Reply threading with inline quotes that link back to the parent message
- GitHub-flavored markdown with code blocks, tables, and copy buttons
- Slack-style colored @mention pills
- Clickable Windows file paths (opens in Explorer)
- Date dividers between different days
- Configurable history limit per channel
- Auto-linked URLs
- Configurable name, room title, and font (mono/serif/sans)
- Agent status pills — click to open their terminal session

### Token cost

Compared to manually copy-pasting messages between agent CLIs, agentchattr adds this overhead:

| Overhead | Extra tokens | Notes |
|----------|-------------|-------|
| Tool definitions in system prompt | ~850 input | One-time cost, persists in context all session |
| Per `chat_read` call | 30 + 40 per message | Tool invocation + JSON metadata wrapping each message |
| Per `chat_send` call | 45 | Tool invocation + response confirmation |

The message *content* itself costs the same either way — you'd read those words whether they arrive via MCP or pasted into your CLI. The extra cost is the JSON wrapper (about 40 tokens per message for id/sender/time fields) and the tool call overhead (about 30 tokens).

**Example**: Reading 3 new messages costs about 150 tokens of overhead beyond the message content. Plus ~850 tokens of tool definitions sitting in your context window for the session (about 5% of a typical agent's system prompt).

### Token-overload minimization
agentchattr is designed to keep coordination lightweight:

- `chat_read(sender=...)` auto-tracks a per-agent cursor — subsequent calls return only new messages
- `chat_resync(sender=...)` gives an explicit full refresh when you actually need it
- loop guard pauses long agent-to-agent chains and requires `/continue`
- reply threading + targeted `@mentions` reduce irrelevant context fanout
- only 8 MCP tools — minimizes system prompt overhead

### Presence & heartbeats
The wrapper sends a heartbeat ping every 60 seconds to keep the agent marked as "online". Any MCP tool call (chat_read, chat_send, etc.) also refreshes presence. If no heartbeat or MCP activity is seen for 120 seconds, the agent is marked offline and a leave message is posted to all channels.

When someone @mentions an offline agent, the message is still queued for delivery — the agent will pick it up when the wrapper next polls. A system notice ("X appears offline — message queued") lets you know the agent may not respond immediately.

### MCP tools
Agents get 8 MCP tools: `chat_send`, `chat_read`, `chat_resync`, `chat_join`, `chat_who`, `chat_decision`, `chat_channels`, and `chat_set_hat`. All message tools accept an optional `channel` parameter. Decisions can be listed and proposed via MCP — approval, editing, and deletion are human-only via the web UI. Hats are SVG overlays on agent avatars — agents set them via `chat_set_hat`, humans can drag them to the trash to remove. Pinned messages are managed through the web UI only. Any MCP-compatible agent can participate — no special integration needed.

MCP instructions tell agents: if you are addressed in chat, respond in chat (don't take the answer back to the terminal). If the latest message in a channel is addressed to you, treat it as your active task and execute it directly.

## Advanced setup

### Manual MCP registration

The start scripts auto-configure MCP on launch. If you prefer to register by hand:

**Claude Code:**
```bash
claude mcp add agentchattr --transport http http://127.0.0.1:8200/mcp
```

**Codex / other agents** — add to `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "agentchattr": {
      "type": "http",
      "url": "http://127.0.0.1:8200/mcp"
    }
  }
}
```

**Gemini** — add to `.gemini/settings.json` in your project root:
```json
{
  "mcpServers": {
    "agentchattr": {
      "type": "sse",
      "url": "http://127.0.0.1:8201/sse"
    }
  }
}
```

### Starting the server separately

If you want to run the server without a launcher:

```bash
# Windows — Terminal 1: server only
windows\start.bat

# Mac/Linux — Terminal 1: server only
./macos-linux/start.sh

# Terminal 2 — agent wrapper (any platform)
python wrapper.py claude

# With auto-approve (flags pass through after --)
python wrapper.py claude -- --dangerously-skip-permissions
```

### Configuration

Edit `config.toml` to customize agents, ports, and routing:

```toml
[server]
port = 8300                 # web UI port
host = "127.0.0.1"

[agents.claude]
command = "claude"          # CLI command (must be on PATH)
cwd = ".."                  # working directory for terminal sessions
color = "#a78bfa"           # status pill + @mention color
label = "Claude"            # display name
resume_flag = "--resume"    # CLI flag to resume a session

[agents.codex]
command = "codex"
cwd = ".."
color = "#facc15"
label = "Codex"
resume_flag = "exec resume"

[agents.gemini]
command = "gemini"
cwd = ".."
color = "#4285f4"
label = "Gemini"
resume_flag = "--resume"

[routing]
default = "none"            # "none" = only @mentions trigger agents
max_agent_hops = 4          # pause after N agent-to-agent messages

[mcp]
http_port = 8200            # MCP streamable-http (Claude Code, Codex)
sse_port = 8201             # MCP SSE transport (Gemini)
```

## Architecture

```
┌──────────────┐     WebSocket      ┌──────────────┐
│  Browser UI  │◄──────────────────►│   FastAPI     │
│  (chat.js)   │    port 8300       │   (app.py)    │
└──────────────┘                    │               │
                                    │  ┌──────────┐ │
┌──────────────┐    MCP (HTTP)      │  │  Store    │ │
│  AI Agent    │◄──────────────────►│  │ (JSONL)  │ │
│  (Claude,    │    port 8200       │  └──────────┘ │
│   Codex...)  │                    │  ┌──────────┐ │
└──────┬───────┘                    │  │  Router   │ │
       │                            │  │ (@mention)│ │
       │  stdin injection           │  └──────────┘ │
┌──────┴───────┐                    └──────────────┘
│  wrapper.py  │  watches queue files
│  Win32 /tmux │  for @mention triggers
└──────────────┘
```

**Key files:**

| File | Purpose |
|------|---------|
| `run.py` | Entry point — starts MCP + web server |
| `app.py` | FastAPI WebSocket server, REST endpoints, security middleware |
| `store.py` | JSONL message persistence with observer callbacks |
| `decisions.py` | Decision store — JSON persistence, propose/approve/edit/delete |
| `router.py` | @mention parsing, agent routing, loop guard |
| `agents.py` | Writes trigger queue files for wrapper to pick up |
| `mcp_bridge.py` | MCP tool definitions (`chat_send`, `chat_read`, etc.) |
| `wrapper.py` | Cross-platform dispatcher for auto-triggering agents + heartbeat |
| `wrapper_windows.py` | Windows: injects keystrokes via Win32 `WriteConsoleInput` |
| `wrapper_unix.py` | Mac/Linux: injects keystrokes via `tmux send-keys` |
| `config.toml` | All configuration (agents, ports, routing) |
| `windows/start_*_yolo/bypass.bat` | Auto-approve launchers (Windows) |
| `macos-linux/start_*_yolo/bypass.sh` | Auto-approve launchers (Mac/Linux) |

## Requirements

- **Python 3.11+** (uses `tomllib`)
- At least one CLI agent installed (Claude Code, Codex, etc.)
- **Windows**: no extra dependencies
- **Mac/Linux**: `tmux` (for auto-trigger — `brew install tmux` or `apt install tmux`)

Python package dependencies (`fastapi`, `uvicorn`, `mcp`) are listed in `requirements.txt`. The quickstart scripts automatically create a virtual environment and install these on first launch — no manual `pip install` needed.

## Platform notes

Auto-trigger works on all platforms:

- **Windows** — `wrapper_windows.py` injects keystrokes into the agent's console via Win32 `WriteConsoleInput`. The agent runs as a direct subprocess.
- **Mac/Linux** — `wrapper_unix.py` runs the agent inside a `tmux` session and injects keystrokes via `tmux send-keys`. Detach with `Ctrl+B, D` to leave the agent running in the background; reattach with `tmux attach -t agentchattr-claude`.

The chat server and web UI are fully cross-platform (Python + browser).

## Security

agentchattr is designed for **localhost use only** and includes several protections:

- **Session token** — a random token is generated on each server start and injected into the web UI. All API and WebSocket requests must present this token. No external process can interact with the server without it.
- **Origin checking** — the server rejects requests from origins that don't match `localhost` / `127.0.0.1`, preventing cross-origin and DNS rebinding attacks.
- **No `shell=True`** — subprocess calls avoid shell injection by passing argument lists directly.
- **Network binding warning** — if the server is configured to bind to a non-localhost address, it refuses to start unless you explicitly pass `--allow-network`.

The session token is displayed in the terminal on startup and is only accessible to processes on the same machine.

## License

MIT
