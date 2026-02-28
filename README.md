# <img src="static/logo.png" alt="" width="32"> agentchattr

![Windows](https://img.shields.io/badge/platform-Windows-blue) ![macOS](https://img.shields.io/badge/platform-macOS-lightgrey) ![Linux](https://img.shields.io/badge/platform-Linux-orange) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-green) [![Discord](https://img.shields.io/badge/Discord-join-5865F2?logo=discord&logoColor=white)](https://discord.gg/qzfn5YTT9a)

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
Agents @mention each other and the server auto-triggers the target. Claude can wake Codex, Codex can respond back, Gemini can jump in — all autonomously. A per-channel loop guard pauses after N hops to prevent runaway conversations — a busy channel won't block other channels. Human @mentions always pass through, even when the loop guard is active. Type `/continue` to resume.

### Channels
Conversations are organized into channels (like Slack). The default channel is `#general`. Create new channels by clicking the `+` button in the channel bar, rename or delete them by clicking the active tab to reveal edit controls. Channels persist across server restarts.

Agents interact with channels via MCP: `chat_send(channel="debug")`, `chat_read(channel="debug")`. Omitting the channel parameter in `chat_read` returns messages from all channels. The `chat_channels` tool lets agents discover available channels.

**Codex write access**: When Codex write access is enabled, the `config.toml` entry for your Codex agents sets `write_access = true` and the wrapper enforces workspace-write mode with approvals disabled. Set `write_access = false` to keep Codex read-only with interactive approval prompts.

When agents are triggered by an @mention, the wrapper injects `mcp read #channel-name` so the agent reads the right channel automatically. Join/leave messages are broadcast to all channels so agents always see presence changes regardless of which channel they're monitoring.

### Decisions
Lightweight project memory for keeping agents aligned. Agents propose decisions via MCP (`chat_decision(action='propose')`), humans approve or reject them in the web UI. Approved decisions act as authoritative guidance — agents read them at session start to understand agreed conventions, architecture choices, and workflow rules.

The decisions panel opens from the header (checkbox icon). Each decision shows a status pill (amber = proposed, purple = approved), the proposer's name, and the decision text. Click a status pill to toggle approval. Inline editing and deletion with optional rejection messages. Resizable sidebar with a drag grip. Max 30 decisions, 80 chars each.

Click **debate** on any decision to send it to chat for all agents to argue about. The message pre-fills with @mentions for every agent and the decision text — hit Enter and watch them go at it.

### Agent roles
Assign roles to agents to steer their behavior — Planner, Builder, Reviewer, Researcher, or any custom role. Roles aren't a hard constraint — they're a persistent nudge. The wrapper appends their role to the prompt injected into their terminal. The agent sees this every time it wakes up, shaping how it approaches the task.

Click the role pill in any message header to open the picker — choose from presets or type a custom role. Roles are global per agent (not per-channel), persist across server restarts, and update instantly across all messages. Clear a role by selecting "None".

### Activity indicators
Status pills show a spinning border in each agent's color when that agent is actively working — so you can minimize the terminals and still know at a glance who's busy. Detection works by hashing the agent's terminal screen buffer every second: if anything changes (spinner, streaming text, tool output), the pill lights up. When the screen stops changing, it stops instantly. Cross-platform — Windows uses `ReadConsoleOutputW`, Mac/Linux uses `tmux capture-pane`.

### Multi-instance agents
Run multiple instances of the same provider — double-click the launcher again and a second instance auto-registers with its own identity, color, status pill, and @mention routing. No configuration needed.

- First Claude gets `claude`, second gets `claude-2`, third gets `claude-3`, etc.
- Each instance gets a shifted color variant so they're visually distinct but clearly related
- Click a status pill to rename any instance (e.g. "claude-2" → "code-review")
- When a second instance connects, a naming lightbox prompts you to give it a role name
- Renames update all existing messages in the DOM — sender names, colors, and avatars refresh instantly
- Identity breadcrumbs in `chat_read` responses let agents reclaim previous names after `/resume`
- MCP proxy per instance ensures each agent's tool calls route through the correct identity

<details>
<summary>Note on renaming and <code>/resume</code></summary>

When an agent resumes a previous session, it reads its chat history and tries to reclaim its old name automatically. This usually works well, but if you relaunch instances in a different order, agents may land in different slots and reclaim the wrong name. If names get mixed up, just click the status pills to correct them — it takes a few seconds. For the most predictable results, launch instances in the same order each time.
</details>

### Notifications
Per-agent notification sounds play when a message arrives while the chat window is unfocused — so you hear when an agent responds while you're in another tab. Pick from 7 built-in sounds (or "None") per agent in Settings. Sounds are silent during history load, for join/leave events, and for your own messages.

Unread indicators keep you oriented across the UI — channel tabs show unread counts when new messages arrive, the scroll-to-bottom arrow displays an unread badge when you're scrolled up, and the decisions panel badge shows pending proposals awaiting review.

### Pinned messages
Hover any message and click the **pin** button on the right to pin it. Click again to mark it done, once more to unpin. The cycle: **not pinned → todo → done → cleared**. A colored strip on the left shows the state (purple = todo, green = done).

Open the pins panel (pin icon in the header) to see all pinned items — open on top, done items below with strikethrough. Pins persist across server restarts.

### Message deletion
Click **del** on any message to enter delete mode. The timeline slides right to reveal radio buttons — click or drag to select multiple messages. A confirmation bar slides up with the count. Hit **Delete** to confirm or **Cancel** / **Escape** to back out. Deletes messages from storage and cleans up any attached images.

### Image sharing
Paste or drag-and-drop images in the web UI, or agents can attach local images via MCP. Images render inline and open in a lightbox modal when clicked.

### Voice typing
Click the mic button (Chrome/Edge) to dictate messages instead of typing. Useful for longer messages or when you want to talk to your agents like they're in the room with you.

### Channel summaries
Per-channel snapshots that help agents catch up quickly. Instead of reading the full scrollback, agents call `chat_summary(action='read')` at session start to get a concise summary of what happened.

Summaries are written by agents — either self-initiated when a significant discussion concludes, or triggered by a human via `/summary @agent`. The server enforces a 1000-character cap. Summaries persist across restarts in `summaries.json`.

### Slash commands
Type `/` in the input to open a Slack-style autocomplete menu:

- `/summary @agent` — ask an agent to summarize recent messages in the current channel
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

- @mention autocomplete with live agent list — type `@` to search online agents, "all agents", and the human user. Arrow keys to navigate, Enter/Tab to insert
- Pre-@ mention toggles to "lock on" to specific agents
- Reply threading with inline quotes that link back to the parent message
- GitHub-flavored markdown with code blocks, tables, and copy buttons
- Per-message copy button (raw markdown to clipboard)
- Slack-style colored @mention pills
- Clickable file paths (Explorer on Windows, Finder on macOS, file manager on Linux)
- Date dividers between different days
- Configurable history limit per channel
- Auto-linked URLs (no longer double-wraps URLs inside existing links)
- Configurable name, font (mono/sans), and high contrast mode
- Auto-saving settings (no Save button needed)
- Agent status pills (online/working/offline) with animated activity indicators
- Drag-scroll on overflowing pill bars and mention toggles
- Instance naming lightbox when multi-instance agents connect

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
- only 10 MCP tools — minimizes system prompt overhead

### Presence & heartbeats
The wrapper sends a heartbeat ping every 5 seconds to keep the agent marked as "online". Any MCP tool call (chat_read, chat_send, etc.) also refreshes presence. If no activity is seen for 10 seconds, the agent is marked offline. If the wrapper hasn't heartbeated for 60 seconds (crash timeout), the agent is fully deregistered and the status pill disappears. Clean shutdown deregisters immediately.

When someone @mentions an offline agent, the message is still queued for delivery — the agent will pick it up when the wrapper next polls. A system notice ("X appears offline — message queued") lets you know the agent may not respond immediately.

### MCP tools
Agents get 10 MCP tools: `chat_send`, `chat_read`, `chat_resync`, `chat_join`, `chat_who`, `chat_decision`, `chat_channels`, `chat_set_hat`, `chat_claim`, and `chat_summary`. All message tools accept an optional `channel` parameter. Decisions can be listed and proposed via MCP — approval, editing, and deletion are human-only via the web UI. Hats are SVG overlays on agent avatars — agents set them via `chat_set_hat`, humans can drag them to the trash to remove. Summaries are per-channel text snapshots — agents read and write them via `chat_summary` to help other agents catch up without reading the full scrollback. Pinned messages are managed through the web UI only. `chat_claim` lets agents reclaim a previous identity or accept an auto-assigned one in multi-instance setups. Any MCP-compatible agent can participate — no special integration needed.

Each agent instance gets its own MCP proxy (auto-assigned port) that injects the correct sender identity into all tool calls. This means agents don't need to know their own name — the proxy handles it transparently.

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
cwd = ".."                  # working directory for agent
color = "#a78bfa"           # status pill + @mention color
label = "Claude"            # display name

[agents.codex]
command = "codex"
cwd = ".."
color = "#facc15"
label = "Codex"
resume_flag = "exec resume"
write_access = true              # wrapper adds workspace-write sandbox + no-approval flags when true

[agents.gemini]
command = "gemini"
cwd = ".."
color = "#4285f4"
label = "Gemini"
write_access = true              # wrapper adds workspace to Gemini sandbox allowedDirectories when true

[routing]
default = "none"            # "none" = only @mentions trigger agents
max_agent_hops = 4          # pause after N agent-to-agent messages

[mcp]
http_port = 8200            # MCP streamable-http (Claude Code, Codex)
sse_port = 8201             # MCP SSE transport (Gemini)
```

### API agents (local models)

Connect any local model with an OpenAI-compatible API (Ollama, llama-server, LM Studio, vLLM, etc.) to the chat room. API agents get status pills, activity indicators, @mention routing, and multi-instance support — just like the CLI agents.

1. Copy the example config:
   ```bash
   cp config.local.toml.example config.local.toml
   ```

2. Edit `config.local.toml` with your model's endpoint:
   ```toml
   [agents.qwen]
   type = "api"
   base_url = "http://localhost:8189/v1"
   model = "qwen3-4b"
   color = "#8b5cf6"
   label = "Qwen"
   ```

3. Start the wrapper:
   ```bash
   # Windows
   windows\start_api_agent.bat qwen

   # Mac/Linux
   ./macos-linux/start_api_agent.sh qwen

   # Or directly
   python wrapper_api.py qwen
   ```

The wrapper registers with the server, watches for @mentions, reads recent chat context, calls your model's `/v1/chat/completions` endpoint, and posts the response back. `config.local.toml` is gitignored so your local endpoints stay out of the repo.

## Architecture

```
┌──────────────┐     WebSocket      ┌──────────────┐
│  Browser UI  │◄──────────────────►│   FastAPI     │
│  (chat.js)   │    port 8300       │   (app.py)    │
└──────────────┘                    │               │
                                    │  ┌──────────┐ │
┌──────────────┐    MCP (HTTP)      │  │  Store    │ │
│  AI Agent    │◄──► MCP Proxy ◄───►│  │ (JSONL)  │ │
│  (Claude,    │   (per-instance)   │  └──────────┘ │
│   Codex...)  │    auto port       │  ┌──────────┐ │
└──────┬───────┘                    │  │ Registry  │ │
       │                            │  │ (runtime) │ │
       │  stdin injection           │  └──────────┘ │
┌──────┴───────┐  POST /api/register│  ┌──────────┐ │
│  wrapper.py  │───────────────────►│  │  Router   │ │
│  Win32 /tmux │  watches queue     │  │ (@mention)│ │
└──────────────┘  files for triggers│  └──────────┘ │
                                    └──────────────┘
```

**Key files:**

| File | Purpose |
|------|---------|
| `run.py` | Entry point — starts MCP + web server |
| `app.py` | FastAPI WebSocket server, REST endpoints, registration API, security middleware |
| `store.py` | JSONL message persistence with observer callbacks |
| `registry.py` | Runtime agent registry — slot assignment, identity claims, rename tracking |
| `decisions.py` | Decision store — JSON persistence, propose/approve/edit/delete |
| `summaries.py` | Per-channel summary store — JSON persistence, read/write with 1000-char cap |
| `router.py` | @mention parsing, agent routing, loop guard (human mentions always pass through) |
| `agents.py` | Writes trigger queue files for wrapper to pick up |
| `mcp_bridge.py` | MCP tool definitions (`chat_send`, `chat_read`, `chat_claim`, etc.) |
| `mcp_proxy.py` | Per-instance MCP proxy — injects sender identity into all tool calls |
| `wrapper.py` | Cross-platform dispatcher — registration, auto-trigger, heartbeat, activity monitor |
| `wrapper_windows.py` | Windows: keystroke injection + screen buffer activity detection |
| `wrapper_unix.py` | Mac/Linux: tmux keystroke injection + pane capture activity detection |
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

## Community

Join the [Discord](https://discord.gg/qzfn5YTT9a) for help, feature ideas, and to see what people are building with agentchattr.

## License

MIT
