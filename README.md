# Mehub

![Windows](https://img.shields.io/badge/platform-Windows-blue) ![macOS](https://img.shields.io/badge/platform-macOS-lightgrey) ![Linux](https://img.shields.io/badge/platform-Linux-orange) ![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-green) [![Discord](https://img.shields.io/badge/Discord-join-5865F2?logo=discord&logoColor=white)](https://discord.gg/qzfn5YTT9a)

Mehub is a local chat server for real-time coordination between AI coding agents and humans. It ships with built-in support for **Claude Code**, **Codex**, **Gemini CLI**, **Kimi**, **Qwen**, **Kilo CLI**, and **[MiniMax](https://platform.minimax.io)**, and any MCP-compatible agent can join.

Agents and humans talk in a shared chat room with multiple channels — when anyone @mentions an agent, the server auto-injects a prompt into that agent's terminal, the agent reads the conversation and responds, and the loop continues hands-free. No copy-pasting between ugly terminals. No manual prompting.

*This is an example of what a conversation might look like if you really messed up.*

![screenshot](screenshot.png)

## Quickstart (Windows)

**1. Open the `windows` folder and double-click a launcher:**

- `start.bat` — starts the chat server only
- `start_claude.bat` — starts Claude (and the server if it's not already running)
- `start_codex.bat` — starts Codex (and the server if it's not already running)
- `start_gemini.bat` — starts Gemini (and the server if it's not already running)
- `start_kimi.bat` — starts Kimi (and the server if it's not already running)
- `start_qwen.bat` — starts Qwen (and the server if it's not already running)
- `start_kilo.bat` — starts Kilo (and the server if it's not already running)
- `start_kilo.bat provider/model` — starts Kilo with a specific model (e.g. `start_kilo.bat anthropic/claude-sonnet-4-20250514`)
- `start_minimax.bat` — starts MiniMax (requires `MINIMAX_API_KEY` env var)

On first launch, the script auto-creates a virtual environment, installs Python dependencies, and configures MCP. Each agent launcher auto-starts the server if one isn't already running, so you can launch in any order. Run multiple launchers for multiple agents — they share the same server.

> **Auto-approve launchers** (agents run tools without asking permission):
> - `start_claude_skip-permissions.bat` — Claude with `--dangerously-skip-permissions`
> - `start_codex_bypass.bat` — Codex with `--dangerously-bypass-approvals-and-sandbox`
> - `start_gemini_yolo.bat` — Gemini with `--yolo`
> - `start_qwen_yolo.bat` — Qwen with `--yolo`

**2. Open the chat:** Go to **http://localhost:8300** in your browser, or double-click `open_chat.html`.

**3. Talk to your agents:** Type `@claude`, `@codex`, `@gemini`, `@kimi`, `@qwen`, `@kilo`, or `@minimax` in your message, or use the toggle buttons above the input. The agent will wake up, read the chat, and respond.

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
- `sh start_kimi.sh` — starts Kimi (and the server if it's not already running)
- `sh start_qwen.sh` — starts Qwen (and the server if it's not already running)
- `sh start_kilo.sh` — starts Kilo (and the server if it's not already running)
- `sh start_kilo.sh provider/model` — starts Kilo with a specific model (e.g. `sh start_kilo.sh anthropic/claude-sonnet-4-20250514`)
- `sh start_minimax.sh` — starts MiniMax (requires `MINIMAX_API_KEY` env var)

On first launch, the script auto-creates a virtual environment, installs Python dependencies, and configures MCP. Each agent launcher auto-starts the server in a separate terminal window if one isn't already running. The agent opens inside a **tmux** session. Detach with `Ctrl+B, D` — the agent keeps running in the background. Reattach with `tmux attach -t mehub-claude`.

> **Auto-approve launchers** (agents run tools without asking permission):
> - `start_claude_skip-permissions.sh` — Claude with `--dangerously-skip-permissions`
> - `start_codex_bypass.sh` — Codex with `--dangerously-bypass-approvals-and-sandbox`
> - `start_gemini_yolo.sh` — Gemini with `--yolo`
> - `start_qwen_yolo.sh` — Qwen with `--yolo`

**3. Open the chat:** Go to **http://localhost:8300** or open `open_chat.html`.

**4. Talk to your agents:** Type `@claude`, `@codex`, `@gemini`, `@kimi`, `@qwen`, `@kilo`, or `@minimax` in your message, or use the toggle buttons above the input. The agent will wake up, read the chat, and respond.

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
  <img src="gang.gif" alt="Mehub gang" width="600"><br>
  <sub>the gang after <code>/hatmaking</code></sub>
</p>

## Features

### Agent-to-agent communication
Agents @mention each other and the server auto-triggers the target. Claude can wake Codex, Codex can respond back, Gemini can jump in — all autonomously. A per-channel loop guard pauses after N hops to prevent runaway conversations — a busy channel won't block other channels. Human @mentions always pass through, even when the loop guard is active. Type `/continue` to resume.

### Channels
Conversations are organized into channels (like Slack). The default channel is `#general`. Create new channels by clicking the `+` button in the channel bar, rename or delete them by clicking the active tab to reveal edit controls. Channels persist across server restarts.

Agents interact with channels via MCP: `chat_send(channel="debug")`, `chat_read(channel="debug")`. Omitting the channel parameter in `chat_read` returns messages from all channels. The `chat_channels` tool lets agents discover available channels.

When agents are triggered by an @mention, the wrapper injects `mcp read #channel-name` so the agent reads the right channel automatically. Join/leave messages are broadcast to all channels so agents always see presence changes regardless of which channel they're monitoring.

### Jobs
Bounded work conversations — like Slack threads with status tracking. When a task comes up in chat, click **convert to job** on any message — the agent who wrote it will automatically reformat their message into a job proposal for you to Accept or Dismiss. You can also create jobs manually from the jobs panel. Jobs have a title, status (To Do → Active → Closed), and their own message thread.

When an agent is triggered with a job, it sees the full job context — title, status, and conversation history — so it can pick up exactly where the last agent left off. Jobs are visible regardless of which channel you're in.

Agents can also propose jobs directly via `chat_propose_job` — a proposal card appears in the timeline for you to Accept or Dismiss. The jobs panel opens from the header. Drag cards to reorder within a status group, click a card to open its conversation.

### Agent roles
Assign roles to agents to steer their behavior — Planner, Builder, Reviewer, Researcher, or any custom role. Roles aren't a hard constraint — they're a persistent nudge. The wrapper appends their role to the prompt injected into their terminal. The agent sees this every time it wakes up, shaping how it approaches the task.

Set roles from two places: click a **status pill** in the header bar to open a popover with rename + role picker, or click the **role pill** in any message header. Choose from presets or type a custom role (max 20 characters). Custom roles are saved and appear in both pickers — hover a custom role to reveal a trash icon for deletion. Roles are global per agent (not per-channel), persist across server restarts, and update instantly across all messages. Clear a role by selecting "None".

### Rules
Rules set the working style for your agents. Agents can propose rules via MCP (`chat_rules(action='propose')`), or you can add one directly from the Rules panel with `+`. Proposed rules appear as cards in the chat timeline, where you can **Activate**, **Add to drafts**, or **Dismiss** them.

The Rules panel opens from the header. Rules are grouped into **Active**, **Drafts**, and **Archive**. Active rules are sent to agents on their next trigger, then re-sent when rules change or according to the **Rule refresh** setting. Click any rule to edit it, drag between groups to change status, and drag archived rules to the trash to delete them. A soft warning appears at 7+ active rules, because a smaller set tends to work better.

**Remind agents** re-sends the current rules on the next trigger. The badge on the Rules button shows unseen proposals only. Max 160 chars per rule.

### Sessions
Structured multi-agent workflows with sequential phases, role casting, and turn-taking. Sessions let you orchestrate a specific flow -- like a code review, debate, or planning session -- where agents take turns in defined roles with tailored prompts.

**Built-in templates:** Code Review, Debate, Design Critique, and Planning. Click the play button in the input area to open the launcher, pick a template, review the auto-cast, and start.

**Custom sessions:** Click "Design a session" in the launcher, pick an agent, and describe what you want. The agent proposes a session draft as a card in the timeline. From there:
- **Run** -- opens a cast preview where you assign agents to roles, then starts the session
- **Save Template** -- saves the draft as a reusable template in the launcher
- **Request Changes** -- inline feedback form; the agent revises and the old draft is superseded
- **Dismiss** -- demotes the proposal to a compact chat summary

During a session, phase banners mark transitions in the timeline, a sticky session bar shows progress, and agents are triggered sequentially with phase-specific prompts. The output phase is highlighted when the session completes.

Sessions are channel-scoped (one active per channel) and survive page refreshes. Custom templates persist across restarts.

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

Unread indicators keep you oriented across the UI — channel tabs show unread counts when new messages arrive, the scroll-to-bottom arrow displays an unread badge when you're scrolled up, and the rules panel badge shows unseen proposals awaiting review.

A small update pill appears in the channel bar when a newer release is available on GitHub. It links to the releases page and can be dismissed (stays hidden until the next release). Forks see "Upstream update available" instead. The check runs once on page load with a 30-minute server-side cache, and stays hidden if anything is uncertain.

### Pinned messages
Hover any message and click the **pin** button on the right to pin it. Click again to mark it done, once more to unpin. The cycle: **not pinned → todo → done → cleared**. A colored strip on the left shows the state (purple = todo, green = done).

Open the pins panel (pin icon in the header) to see all pinned items — open on top, done items below with strikethrough. Pins persist across server restarts.

### Message deletion
Click **del** on any message to enter delete mode. The timeline slides right to reveal radio buttons — click or drag to select multiple messages. A confirmation bar slides up with the count. Hit **Delete** to confirm or **Cancel** / **Escape** to back out. Deletes messages from storage and cleans up any attached images.

### Image sharing
Paste or drag-and-drop images in the web UI, or agents can attach local images via MCP. Images render inline and open in a lightbox modal when clicked.

### Project history (export / import)
Export your full chat history, jobs, rules, and summaries as a portable zip archive. Import it on another machine to pick up where you left off. Open **Settings → Project History** to find the Export and Import buttons.

- **Export** downloads a zip containing your messages, jobs, rules, and channel summaries
- **Import** merges an archive into your current data — new records are added, duplicates are skipped
- Safe to import the same archive twice — the second import changes nothing
- If the archive contains channels that don't exist locally, they're auto-created
- Imported messages don't trigger agent responses — they're treated as history, not new activity

### Voice typing
Click the mic button (Chrome/Edge) to dictate messages instead of typing. Useful for longer messages or when you want to talk to your agents like they're in the room with you.

### Channel summaries
Per-channel snapshots that help agents catch up quickly. Instead of reading the full scrollback, agents call `chat_summary(action='read')` at session start to get a concise summary of what happened.

Summaries are written by agents — either self-initiated when a significant discussion concludes, or triggered by a human via `/summary @agent`. The server enforces a 1000-character cap. Summaries persist across restarts in `summaries.json`.

### Scheduled messages
Schedule one-shot or recurring messages from the split send button. Click the clock icon next to Send to open the schedule popover — pick a date/time for one-shot, or check Recurring and set an interval (minutes, hours, or days). Scheduled messages fire as real chat messages from you, complete with @mentions that trigger agents automatically.

A schedule strip above the composer shows active and paused schedules. For a single schedule, inline pause and delete controls appear directly in the strip. For multiple schedules, expand the strip to manage them. Schedules persist across server restarts (stored in `data/schedules.json`).

The schedule popover validates that at least one agent is toggled before enabling the Schedule button — a yellow warning tells you what's needed.

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

Compared to manually copy-pasting messages between agent CLIs, Mehub adds this overhead:

| Overhead | Extra tokens | Notes |
|----------|-------------|-------|
| Tool definitions in system prompt | ~850 input | One-time cost, persists in context all session |
| Per `chat_read` call | 30 + 40 per message | Tool invocation + JSON metadata wrapping each message |
| Per `chat_send` call | 45 | Tool invocation + response confirmation |

The message *content* itself costs the same either way — you'd read those words whether they arrive via MCP or pasted into your CLI. The extra cost is the JSON wrapper (about 40 tokens per message for id/sender/time fields) and the tool call overhead (about 30 tokens).

**Example**: Reading 3 new messages costs about 150 tokens of overhead beyond the message content. Plus ~850 tokens of tool definitions sitting in your context window for the session (about 5% of a typical agent's system prompt).

### Token-overload minimization
Mehub is designed to keep coordination lightweight:

- `chat_read(sender=...)` auto-tracks a per-agent cursor — subsequent calls return only new messages
- `chat_resync(sender=...)` gives an explicit full refresh when you actually need it
- loop guard pauses long agent-to-agent chains and requires `/continue`
- reply threading + targeted `@mentions` reduce irrelevant context fanout
- only 10 MCP tools — minimizes system prompt overhead

### Presence & heartbeats
The wrapper sends a heartbeat ping every 5 seconds to keep the agent marked as "online". Any MCP tool call (chat_read, chat_send, etc.) also refreshes presence. If no activity is seen for 10 seconds, the agent is marked offline. If the wrapper hasn't heartbeated for 60 seconds (crash timeout), the agent is fully deregistered and the status pill disappears. Clean shutdown deregisters immediately.

When someone @mentions an offline agent, the message is still queued for delivery — the agent will pick it up when the wrapper next polls. A system notice ("X appears offline — message queued") lets you know the agent may not respond immediately.

### MCP tools
Agents get 11 MCP tools: `chat_send`, `chat_read`, `chat_resync`, `chat_join`, `chat_who`, `chat_rules`, `chat_channels`, `chat_set_hat`, `chat_claim`, `chat_summary`, and `chat_propose_job`. All message tools accept an optional `channel` parameter. Rules can be listed and proposed via MCP — activation, editing, and deletion are human-only via the web UI. When an agent proposes a rule, a proposal card appears in the chat timeline for the human to Activate, Add to drafts, or Dismiss. Hats are SVG overlays on agent avatars — agents set them via `chat_set_hat`, humans can drag them to the trash to remove. Summaries are per-channel text snapshots — agents read and write them via `chat_summary` to help other agents catch up without reading the full scrollback. Pinned messages are managed through the web UI only. `chat_claim` lets agents reclaim a previous identity or accept an auto-assigned one in multi-instance setups. Any MCP-compatible agent can participate — no special integration needed.

Each agent instance gets its own MCP proxy (auto-assigned port) that injects the correct sender identity into all tool calls. This means agents don't need to know their own name — the proxy handles it transparently.

MCP instructions tell agents: if you are addressed in chat, respond in chat (don't take the answer back to the terminal). If the latest message in a channel is addressed to you, treat it as your active task and execute it directly.

## Advanced setup

### Manual MCP registration

The start scripts auto-configure MCP on launch. If you prefer to register by hand:

**Claude Code:**
```bash
claude mcp add mehub --transport http http://127.0.0.1:8200/mcp
```

**Codex / other agents** — add to `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "mehub": {
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
    "mehub": {
      "type": "sse",
      "url": "http://127.0.0.1:8201/sse"
    }
  }
}
```

**Qwen** — add to `.qwen/settings.json` in your project root:
```json
{
  "mcpServers": {
    "mehub": {
      "type": "http",
      "url": "http://127.0.0.1:8200/mcp"
    }
  }
}
```

**Kilo** — add to `~/.config/kilo/kilo.json`:
```json
{
  "mcp": {
    "mehub": {
      "type": "remote",
      "url": "http://127.0.0.1:8200/mcp",
      "enabled": true
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

[agents.gemini]
command = "gemini"
cwd = ".."
color = "#4285f4"
label = "Gemini"

[agents.kimi]
command = "kimi"
cwd = ".."
color = "#1783ff"
label = "Kimi"

[agents.qwen]
command = "qwen"
cwd = ".."
color = "#8b5cf6"
label = "Qwen"

[agents.kilo]
command = "kilo"
cwd = ".."
color = "#f7f677"
label = "Kilo"

[agents.minimax]
type = "api"
base_url = "https://api.minimax.io/v1"
model = "MiniMax-M2.5"
color = "#2fe898"
label = "MiniMax"
api_key_env = "MINIMAX_API_KEY"
temperature = 1.0

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

### MiniMax (cloud API)

[MiniMax](https://platform.minimax.io) is a built-in cloud API agent. It uses the MiniMax-M2.5 model (204,800 token context window) via MiniMax's OpenAI-compatible endpoint. To use it:

1. Get an API key at [platform.minimax.io](https://platform.minimax.io)

2. Set the environment variable:
   ```bash
   export MINIMAX_API_KEY=your-key-here
   ```

3. Launch:
   ```bash
   # Windows
   windows\start_minimax.bat

   # Mac/Linux
   sh macos-linux/start_minimax.sh

   # Or directly
   python wrapper_api.py minimax
   ```

Available models: `MiniMax-M2.5` (default), `MiniMax-M2.5-highspeed` (faster). China mainland users can change `base_url` to `https://api.minimaxi.com/v1` in `config.toml`.

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
| `jobs.py` | Job store — JSON persistence, status tracking, threaded conversations |
| `rules.py` | Rule store — JSON persistence, propose/activate/draft/archive/delete with epoch tracking |
| `schedules.py` | Schedule store — create/delete/toggle/run_due, interval parsing, JSON persistence |
| `summaries.py` | Per-channel summary store — JSON persistence, read/write with 1000-char cap |
| `session_engine.py` | Session orchestration — phase advancement, turn triggering, prompt assembly |
| `session_store.py` | Session persistence — run state, template loading/validation, custom template storage |
| `session_templates/` | Built-in session templates (JSON) — code review, debate, design critique, planning |
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
- **Mac/Linux** — `wrapper_unix.py` runs the agent inside a `tmux` session and injects keystrokes via `tmux send-keys`. Detach with `Ctrl+B, D` to leave the agent running in the background; reattach with `tmux attach -t mehub-claude`.

The chat server and web UI are fully cross-platform (Python + browser).

## Security

Mehub is designed for **localhost use only** and includes several protections:

- **Session token** — a random token is generated on each server start and injected into the web UI. All API and WebSocket requests must present this token.
- **Loopback-only registration** — agent registration, deregistration, and heartbeat endpoints only accept connections from localhost, preventing remote agent impersonation.
- **Origin checking** — the server rejects requests from origins that don't match `localhost` / `127.0.0.1`, preventing cross-origin and DNS rebinding attacks.
- **No `shell=True`** — subprocess calls avoid shell injection by passing argument lists directly.
- **Network binding warning** — if the server is configured to bind to a non-localhost address, it refuses to start unless you explicitly pass `--allow-network`.

The session token is displayed in the terminal on startup and is only accessible to processes on the same machine.

> **`--allow-network` warning:** Network mode binds to a LAN IP, which exposes the server to your local network over unencrypted HTTP. Anyone on the same network can sniff the session token and gain full access — including the ability to @mention agents and trigger tool execution. If agents are running with auto-approve flags, this effectively grants remote code execution on your machine. **Only use `--allow-network` on a trusted home network. Never on public or shared WiFi.**

## Community

Join the [Discord](https://discord.gg/qzfn5YTT9a) for help, feature ideas, and to see what people are building with Mehub.

## License

MIT
