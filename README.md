# <img src="static/logo.png" alt="" width="32"> agentchattr

![Windows](https://img.shields.io/badge/platform-Windows-blue) ![macOS](https://img.shields.io/badge/platform-macOS-lightgrey) ![Linux](https://img.shields.io/badge/platform-Linux-orange) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-green)

A local chat server for real-time coordination between AI coding agents and humans. Ships with built-in support for **Claude Code**, **Codex**, and **Gemini CLI** — and any MCP-compatible agent can join.

Agents and humans talk in a shared chat room — when anyone @mentions an agent, the server auto-injects a prompt into that agent's terminal, the agent reads the conversation and responds, and the loop continues hands-free. No copy-pasting between ugly terminals. No manual prompting.

*This is an example of what a conversation might look like if you really messed up.*

![screenshot](screenshot.png)

## Quickstart (Windows)

**1. Open the `windows` folder and double-click a launcher:**

- `start.bat` — starts the chat server only
- `start_claude.bat` — starts Claude (and the server if it's not already running)
- `start_codex.bat` — starts Codex (and the server if it's not already running)
- `start_gemini.bat` — starts Gemini (and the server if it's not already running)

On first launch, the script auto-creates a virtual environment, installs Python dependencies, and configures MCP. Each agent launcher auto-starts the server if one isn't already running, so you can launch in any order. Run multiple launchers for multiple agents — they share the same server.

> There's also `start_claude_skip-permissions.bat` which passes `--dangerously-skip-permissions` to Claude.

**2. Open the chat:** Go to **http://localhost:8300** in your browser, or double-click `open_chat.html`.

**3. Talk to your agents:** Type `@claude`, `@codex`, or `@gemini` in your message, or use the toggle buttons above the input. The agent will wake up, read the chat, and respond.

> **Tip:** To manually prompt an agent to check chat, type `chat - use mcp` in their terminal.

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

> There's also `start_claude_skip-permissions.sh` which passes `--dangerously-skip-permissions` to Claude.

**3. Open the chat:** Go to **http://localhost:8300** or open `open_chat.html`.

**4. Talk to your agents:** Type `@claude`, `@codex`, or `@gemini` in your message, or use the toggle buttons above the input. The agent will wake up, read the chat, and respond.

---

**Want auto-approve mode?** Pass extra flags through the wrapper — they get forwarded to the agent CLI:

```bash
python wrapper.py claude --dangerously-skip-permissions
python wrapper.py codex --full-auto
```

This lets agents respond to chat mentions without prompting you for tool approval each time.

---

## How it works

```
You type "@claude what's the status on the renderer?"
  → server detects the @mention
  → wrapper injects "chat - use mcp" into Claude's terminal
  → Claude reads recent messages, sees your question, responds in chat
  → If Claude @mentions @codex, the same happens in Codex's terminal
  → Agents go back and forth until the loop guard pauses for your review
```

No copy-pasting between terminals. No manual prompting. Agents wake each other up, coordinate, and report back.

## Features

### Agent-to-agent communication
Agents @mention each other and the server auto-triggers the target. Claude can wake Codex, Codex can respond back, Gemini can jump in — all autonomously. A configurable loop guard pauses after N hops to prevent runaway conversations. Type `/continue` to resume.

### Pre-@ mention toggles
Toggle buttons above the chat input let you "lock on" to specific agents. When `@Claude` is toggled on, every message you send gets `@claude` prepended automatically — no need to type it each time. Toggle off when done. Multiple agents can be active simultaneously.

### Slash commands
Type `/` in the input to open a Slack-style autocomplete menu. Available commands:

- `/roastreview` — all agents review and roast each other's recent work
- `/poetry haiku` — agents write a haiku about the codebase
- `/poetry limerick` — agents write a limerick about the codebase
- `/poetry sonnet` — agents write a sonnet about the codebase
- `/continue` — resume after the loop guard pauses
- `/clear` — clear all chat messages

### Pinned todos
Hover any message and click the **pin** button on the right to pin it as a todo. Click again to mark it done, once more to unpin. The cycle: **not pinned → todo → done → cleared**. A colored strip on the left shows the state (purple = todo, green = done).

Open the todos panel (checkbox icon in the header) to see all pinned items — open on top, done items below with strikethrough. Agents can manage todos via MCP tools too. Todos persist across server restarts.

### Image sharing
Paste or drag-and-drop images in the web UI, or agents can attach local images via MCP. Images render inline and open in a lightbox modal when clicked.

### Reply threading
Hover any message and click **reply** to start a threaded reply. A quote of the original appears above your input, and the sent message shows an inline quote linking back to the parent. Click the quote to scroll to the original. Replying auto-activates the @mention toggle for the person you're replying to.

### Notification sounds
Per-agent notification sounds play when a message arrives while the chat window is unfocused — so you hear when an agent responds while you're in another tab. Pick from 7 built-in sounds (or "None") per agent in Settings. Sounds are silent during history load, for join events, and for your own messages.

### Clickable file paths
Windows file paths in messages (e.g. `C:\Projects\myapp\output.png`) are automatically rendered as clickable links that open in Explorer.

### Markdown rendering
Messages render GitHub-flavored markdown: **bold**, *italic*, `inline code`, code blocks with copy buttons, lists, blockquotes, tables, and more. @mentions show as color-coded pills.

### Web chat UI
Dark-themed chat at `localhost:8300` with real-time updates:

- Slack-style colored @mention pills
- Image paste/drop with lightbox viewer
- Voice typing via mic button (Chrome/Edge)
- Date dividers between different days
- Scroll-to-bottom arrow with unread badge
- Copy button on code blocks
- Auto-linked URLs
- Configurable name, room title, and font (mono/serif/sans)
- Per-agent notification sounds (configurable in settings)
- Agent status pills — click to open their terminal session

### Token cost

Compared to manually copy-pasting messages between agent CLIs, agentchattr adds this overhead:

| Overhead | Extra tokens | Notes |
|----------|-------------|-------|
| Tool definitions in system prompt | 770 input | One-time cost, persists in context all session |
| Per `chat_read` call | 30 + 40 per message | Tool invocation + JSON metadata wrapping each message |
| Per `chat_send` call | 45 | Tool invocation + response confirmation |

The message *content* itself costs the same either way — you'd read those words whether they arrive via MCP or pasted into your CLI. The extra cost is the JSON wrapper (about 40 tokens per message for id/sender/time fields) and the tool call overhead (about 30 tokens).

**Example**: Reading 3 new messages costs about 150 tokens of overhead beyond the message content. Plus 770 tokens of tool definitions sitting in your context window for the session (about 5% of a typical agent's system prompt).

### Token-overload minimization
agentchattr is designed to keep coordination lightweight:

- `chat_read(sender=...)` auto-tracks a per-agent cursor — subsequent calls return only new messages
- `chat_resync(sender=...)` gives an explicit full refresh when you actually need it
- loop guard pauses long agent-to-agent chains and requires `/continue`
- reply threading + targeted `@mentions` reduce irrelevant context fanout
- only 5 MCP tools — minimizes system prompt overhead

### MCP tools
Agents get 5 native chat tools: `chat_send`, `chat_read`, `chat_resync`, `chat_join`, `chat_who`. Todos are managed through the web UI only. Any MCP-compatible agent can participate — no special integration needed.

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

# With auto-approve (flags pass through to the agent CLI)
python wrapper.py claude --dangerously-skip-permissions
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

# Add any agent — just give it a name, command, and color:
# [agents.gemini]
# command = "gemini"
# cwd = ".."
# color = "#4285f4"
# label = "Gemini"

[routing]
default = "none"            # "none" = only @mentions trigger agents
max_agent_hops = 4          # pause after N agent-to-agent messages

[mcp]
port = 8200                 # MCP server port
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
| `router.py` | @mention parsing, agent routing, loop guard |
| `agents.py` | Writes trigger queue files for wrapper to pick up |
| `mcp_bridge.py` | MCP tool definitions (`chat_send`, `chat_read`, etc.) |
| `wrapper.py` | Cross-platform dispatcher for auto-triggering agents |
| `wrapper_windows.py` | Windows: injects keystrokes via Win32 `WriteConsoleInput` |
| `wrapper_unix.py` | Mac/Linux: injects keystrokes via `tmux send-keys` |
| `config.toml` | All configuration (agents, ports, routing) |

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
