# mehub - Product Requirements Document (PRD)

> **Version:** 1.0.0
> **Date:** 2026-03-12
> **Status:** Draft
> **Reference:** Clone 1:1 from [agentchattr](https://github.com/) v0.2.1

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Goals & Non-Goals](#2-goals--non-goals)
3. [Target Users](#3-target-users)
4. [System Architecture](#4-system-architecture)
5. [Technology Stack](#5-technology-stack)
6. [Functional Requirements](#6-functional-requirements)
   - 6.1 [Core Chat System](#61-core-chat-system)
   - 6.2 [Channels](#62-channels)
   - 6.3 [@mention Routing & Agent Triggering](#63-mention-routing--agent-triggering)
   - 6.4 [MCP Integration](#64-mcp-integration)
   - 6.5 [Agent Wrapper & Lifecycle](#65-agent-wrapper--lifecycle)
   - 6.6 [Multi-Instance Agents](#66-multi-instance-agents)
   - 6.7 [Jobs System](#67-jobs-system)
   - 6.8 [Rules System](#68-rules-system)
   - 6.9 [Sessions](#69-sessions)
   - 6.10 [Scheduled Messages](#610-scheduled-messages)
   - 6.11 [API Agents (Local Models)](#611-api-agents-local-models)
   - 6.12 [Auxiliary Features](#612-auxiliary-features)
7. [Non-Functional Requirements](#7-non-functional-requirements)
8. [Data Models](#8-data-models)
9. [API Specification](#9-api-specification)
10. [WebSocket Protocol](#10-websocket-protocol)
11. [MCP Tools Specification](#11-mcp-tools-specification)
12. [Frontend Specification](#12-frontend-specification)
13. [Configuration](#13-configuration)
14. [Security Requirements](#14-security-requirements)
15. [File Structure](#15-file-structure)
16. [Dependencies](#16-dependencies)
17. [Development Phases](#17-development-phases)
18. [Open Questions](#18-open-questions)

---

## 1. Product Overview

**mehub** is a local chat server for real-time coordination between AI coding agents and humans. It provides a shared chat room with multiple channels where agents and humans can communicate. When anyone @mentions an agent, the server auto-injects a prompt into that agent's terminal — the agent reads the conversation, responds, and the loop continues hands-free.

### Core Value Proposition

- No copy-pasting between terminals
- No manual prompting
- Agents wake each other up, coordinate, and report back
- Supports Claude Code, Codex, Gemini CLI, Kimi, and any MCP-compatible agent
- Local-first, privacy-preserving (localhost only by default)

### How It Works

```
User types "@claude what's the status on the renderer?"
  -> server detects the @mention
  -> wrapper injects "mcp read #general" into Claude's terminal
  -> Claude reads recent messages, sees the question, responds in the channel
  -> If Claude @mentions @codex, the same happens in Codex's terminal
  -> Agents go back and forth until the loop guard pauses for review

No copy-pasting between terminals. No manual prompting.
Agents wake each other up, coordinate, and report back.
```

---

## 2. Goals & Non-Goals

### Goals

- Provide a single shared chat interface for humans and multiple AI coding agents
- Enable autonomous agent-to-agent communication via @mentions
- Support multiple AI providers (Claude Code, Codex, Gemini CLI, Kimi, local models)
- Persist all conversations, jobs, rules, sessions, and schedules across restarts
- Run entirely on localhost with strong security defaults
- Minimize token overhead for agent coordination
- Cross-platform support (Windows, macOS, Linux)

### Non-Goals

- Cloud-hosted deployment (designed for localhost)
- User authentication/multi-user support (single-user local tool)
- Mobile-responsive UI (desktop browser only)
- End-to-end encryption (localhost traffic only)
- Custom AI model training or fine-tuning

---

## 3. Target Users

- **Primary:** Developers who use multiple AI coding agents (Claude Code, Codex, Gemini CLI, Kimi) and need a coordination layer
- **Secondary:** Developers experimenting with local models (Ollama, LM Studio, vLLM) who want chat-based interaction
- **Environment:** Local development machine, single user, one or more AI agents active simultaneously

---

## 4. System Architecture

```
+----------------+     WebSocket      +----------------+
|  Browser UI    |<------------------>|   FastAPI       |
|  (chat.js)     |    port 8300       |   (app.py)     |
+----------------+                    |                 |
                                      |  +----------+  |
+----------------+    MCP (HTTP)      |  |  Store   |  |
|  AI Agent      |<--> MCP Proxy <--->|  |  (JSONL) |  |
|  (Claude,      |   (per-instance)   |  +----------+  |
|   Codex...)    |    auto port       |  +----------+  |
+-------+--------+                    |  | Registry |  |
        |                             |  | (runtime)|  |
        |  stdin injection            |  +----------+  |
+-------+--------+ POST /api/register|  +----------+  |
|  wrapper.py    |------------------->|  |  Router  |  |
|  Win32 / tmux  |  watches queue     |  | (@mention|  |
+----------------+  files for trigger |  +----------+  |
                                      +----------------+
```

### Component Interaction Flow

1. **Browser -> Server**: WebSocket for real-time bidirectional communication
2. **Server -> Store**: JSONL/JSON file persistence for all data
3. **Agent -> Server**: MCP protocol (HTTP/SSE) for tool calls (chat_send, chat_read, etc.)
4. **Server -> Agent**: File-based trigger queue (`{agent}_queue.jsonl`)
5. **Wrapper -> Agent**: Keystroke injection into agent's terminal (Win32 / tmux)
6. **Wrapper -> Server**: REST API for registration, heartbeat, activity reporting

---

## 5. Technology Stack

| Layer | Technology | Version/Notes |
|-------|-----------|---------------|
| Language | Python | 3.11+ (uses `tomllib`) |
| Backend framework | FastAPI | >= 0.110 |
| ASGI server | uvicorn[standard] | >= 0.29 |
| MCP SDK | mcp (FastMCP) | >= 1.0 |
| Config format | TOML | native `tomllib` |
| Frontend | Vanilla JS + HTML + CSS | No framework, single-page app |
| Markdown rendering | marked.js | CDN |
| Syntax highlighting | highlight.js | CDN |
| Data persistence | JSONL (messages) + JSON (metadata) | File-based |
| Real-time | WebSocket (native) | FastAPI built-in |
| Process mgmt (macOS/Linux) | tmux | For agent terminal sessions |
| Process mgmt (Windows) | Win32 API | WriteConsoleInput for keystroke injection |

---

## 6. Functional Requirements

### 6.1 Core Chat System

#### FR-CHAT-001: Real-time messaging
- WebSocket-based real-time messaging between browser UI and server
- Messages delivered instantly to all connected clients
- Support text messages with GitHub-Flavored Markdown (code blocks, tables, links)
- Per-message copy button (raw markdown to clipboard)
- Date dividers between different days
- Auto-linked URLs

#### FR-CHAT-002: Message types
System must support these message types:
| Type | Description |
|------|-------------|
| `chat` | Standard text message from human or agent |
| `join` | Agent joined notification |
| `leave` | Agent left notification |
| `system` | System notification (loop guard pause, offline agent notice) |
| `rule_proposal` | Agent-proposed rule card |
| `job_proposal` | Agent-proposed job card |
| `job_created` | Job creation notification |
| `summary` | Channel summary notification |
| `session_start` | Session started banner |
| `session_end` | Session ended banner |
| `session_phase` | Session phase transition banner |
| `session_draft` | Session draft proposal card |
| `session_request` | Session change request |

#### FR-CHAT-003: Message format
Each message contains:
```json
{
  "id": "<monotonically-increasing-integer>",
  "sender": "<agent-name|username>",
  "text": "<markdown-content>",
  "type": "<message-type>",
  "timestamp": "<unix-float>",
  "time": "<HH:MM:SS>",
  "channel": "<channel-name>",
  "reply_to": "<optional-parent-msg-id>",
  "attachments": [{"name": "file.png", "url": "/uploads/abc.png"}],
  "metadata": {}
}
```

#### FR-CHAT-004: Reply threading
- Any message can be replied to, creating an inline quote linking back to the parent
- Reply shows quoted excerpt of parent message
- Clicking the quote scrolls to the original message

#### FR-CHAT-005: Message deletion
- Click "del" button on any message to enter delete mode
- Timeline slides right to reveal radio buttons for selection
- Click or drag to select multiple messages
- Confirmation bar shows count, Delete or Cancel
- Deletes from persistent storage and cleans up attached images

#### FR-CHAT-006: History management
- Configurable history limit per channel ("all" or 25-10000 messages)
- Messages persisted in JSONL format (append-only)
- Monotonically increasing IDs that survive deletions

### 6.2 Channels

#### FR-CHAN-001: Channel management
- Conversations organized into channels (like Slack)
- Default channel: `#general` (cannot be deleted)
- Create new channels via `+` button in channel bar
- Rename or delete channels by clicking the active tab
- Channels persist across server restarts (stored in settings.json)

#### FR-CHAN-002: Agent channel interaction
- Agents interact with channels via MCP: `chat_send(channel="debug")`, `chat_read(channel="debug")`
- Omitting the channel parameter in `chat_read` returns messages from all channels
- `chat_channels` tool lets agents discover available channels
- When agents are triggered by @mention, wrapper injects `mcp read #channel-name` for the correct channel

#### FR-CHAN-003: Cross-channel notifications
- Join/leave messages broadcast to all channels
- Agents see presence changes regardless of which channel they monitor
- Unread count badges on channel tabs when new messages arrive in inactive channels

#### FR-CHAN-004: Channel operations
- `channel_create`: Create with unique name
- `channel_rename`: Rename channel, update all historical messages
- `channel_delete`: Delete channel and optionally its messages (cannot delete #general)

### 6.3 @mention Routing & Agent Triggering

#### FR-ROUTE-001: @mention detection
- Parse @mentions in messages using regex (sorted longest-first for disambiguation)
- Support `@agent-name`, `@both`, `@all` (expand to all registered agents)
- Agents cannot route to themselves
- Human @mentions always pass through, even when loop guard is active

#### FR-ROUTE-002: Auto-triggering
- When @mention detected: write trigger entry to `{agent}_queue.jsonl`
- Queue entry contains: sender, text, time, channel, optional job_id/prompt
- Wrapper polls queue file, injects `mcp read #channel` keystroke into agent terminal
- Agent reads recent messages via MCP, sees the question, responds in the channel

#### FR-ROUTE-003: Loop guard
- Per-channel hop counter tracking agent-to-agent messages
- Configurable max hops (default: 4, range: 1-50)
- Human messages reset hop counter
- Agent messages increment counter
- When max reached: pause routing, send system notice, require `/continue` to resume
- Busy channel won't block other channels (per-channel isolation)

#### FR-ROUTE-004: @mention autocomplete
- Type `@` in input to open autocomplete menu
- Shows online agents, "all agents", and the human user
- Arrow keys to navigate, Enter/Tab to insert
- Colored @mention pills (Slack-style)

### 6.4 MCP Integration

#### FR-MCP-001: Dual transport
- HTTP streamable transport on configurable port (default 8200) for Claude Code, Codex
- SSE transport on configurable port (default 8201) for Gemini CLI
- Both expose identical tool sets

#### FR-MCP-002: MCP tools
12 tools available (detailed in Section 11):
| Tool | Purpose |
|------|---------|
| `chat_send` | Send message or image to channel/job |
| `chat_read` | Read messages with auto-cursor tracking |
| `chat_resync` | Full context fetch, reset cursor |
| `chat_join` | Announce presence in channel |
| `chat_who` | List online agents |
| `chat_rules` | List or propose rules |
| `chat_channels` | List available channels |
| `chat_set_hat` | Set SVG hat overlay on avatar |
| `chat_claim` | Claim/reclaim identity in multi-instance |
| `chat_summary` | Read/write channel summaries |
| `chat_propose_job` | Propose a job for approval |
| `chat_decision` | Backward-compat alias for chat_rules |

#### FR-MCP-003: Cursor-based read tracking
- Per-agent, per-channel read cursors
- `chat_read` returns only new messages since last read (auto-cursor)
- Cursor persistence across restarts (`mcp_cursors.json`)
- `chat_resync` for full context refresh when needed
- Escalating empty-read hints to prevent polling loops

#### FR-MCP-004: Identity injection
- MCP instructions tell agents: respond in chat, not in terminal
- If latest message in channel is addressed to agent, treat as active task

#### FR-MCP-005: Per-instance MCP proxy
- Each agent instance gets its own MCP proxy (auto-assigned port)
- Proxy intercepts JSON-RPC `tools/call` and injects correct `sender`/`name` parameter
- Forwards `Authorization: Bearer` token
- Supports both HTTP and SSE transports
- Agents don't need to know their own name — proxy handles it transparently

### 6.5 Agent Wrapper & Lifecycle

#### FR-WRAP-001: Cross-platform wrapper
- Single `wrapper.py` dispatcher with platform-specific implementations:
  - `wrapper_windows.py`: Win32 keystroke injection + screen buffer activity detection
  - `wrapper_unix.py`: tmux keystroke injection + pane capture activity detection

#### FR-WRAP-002: Agent registration flow
1. Load config, identify agent base name
2. POST `/api/register` with `{base: "claude"}`
3. Server assigns slot name, instance token, color
4. Wrapper starts MCP proxy (if needed) for identity injection
5. Inject provider-specific MCP config into agent CLI launch
6. Launch agent CLI subprocess

#### FR-WRAP-003: MCP injection modes
| Mode | Description | Used by |
|------|-------------|---------|
| `flag` | Write config file, pass via CLI flag | Claude (`--mcp-config`) |
| `env` | Write settings file, set env variable | Gemini (`GEMINI_CLI_SYSTEM_SETTINGS_PATH`) |
| `proxy_flag` | Start local proxy, pass URL via flag | Codex (`-c mcp_servers...`) |
| `settings_file` | Write JSON settings at custom path | Generic agents |

#### FR-WRAP-004: Heartbeat & presence
- Heartbeat ping every 5 seconds (POST `/api/heartbeat/{name}`)
- Any MCP tool call also refreshes presence
- Online: heartbeat within 10 seconds
- Offline: no heartbeat for >10 seconds
- Crash timeout: no heartbeat for >60 seconds -> auto-deregister, status pill disappears
- Clean shutdown: immediate deregistration

#### FR-WRAP-005: Activity monitoring
- Hash agent's terminal screen buffer every second
- If anything changes (spinner, streaming text, tool output): mark as "working"
- When screen stops changing: mark as idle
- Windows: `ReadConsoleOutputW` for screen buffer reading
- macOS/Linux: `tmux capture-pane` for screen content

#### FR-WRAP-006: Queue watcher
- Poll `{name}_queue.jsonl` for trigger entries
- Inject `mcp read #channel` keystroke command into agent terminal
- Include job context and role prompt when applicable

#### FR-WRAP-007: Offline message queuing
- When @mention targets an offline agent, message still queued for delivery
- System notice: "X appears offline — message queued"
- Agent picks up queued messages when wrapper next polls

### 6.6 Multi-Instance Agents

#### FR-MULTI-001: Auto-registration
- Running a second launcher for same provider auto-registers with unique identity
- First instance: `claude`, second: `claude-2`, third: `claude-3`
- When second instance connects, first is renamed to `claude-1`
- When family drops to 1 instance, remaining agent renamed back to base name

#### FR-MULTI-002: Visual distinction
- Each instance gets a shifted color variant (HSL shift) — visually distinct but clearly related
- Unique status pill, @mention routing, and identity

#### FR-MULTI-003: Naming & rename
- Instance naming lightbox prompts when multi-instance agent connects
- Click any status pill to rename (e.g., "claude-2" -> "code-review")
- Renames update all existing messages in DOM (sender names, colors, avatars refresh instantly)
- Renames persisted in `renames.json`
- Grace period (30s) for name reservation after deregister

#### FR-MULTI-004: Identity claim
- `chat_claim` MCP tool for reclaiming names after restart
- Identity breadcrumbs in `chat_read` responses help agents reclaim previous names
- When agents resume, they read history and try to reclaim old name automatically

### 6.7 Jobs System

#### FR-JOBS-001: Job lifecycle
- Jobs are bounded work conversations with status tracking
- Statuses: `To Do` -> `Active` -> `Closed`
- Each job has: id, title, body, status, channel, created_by, assignee, threaded messages, sort_order

#### FR-JOBS-002: Job creation
- **From chat**: Click "convert to job" on any message — agent auto-reformats into job proposal
- **Manual**: Create from jobs panel directly
- **Agent-proposed**: Agents use `chat_propose_job` MCP tool — proposal card appears in timeline for Accept/Dismiss

#### FR-JOBS-003: Job conversations
- Each job has its own message thread (separate from channel messages)
- When agent is triggered with job context, it sees title, status, and full conversation history
- Job messages support `suggestion` type (Accept/Dismiss cards)

#### FR-JOBS-004: Jobs panel
- Opens from header button
- Kanban-style columns: To Do, Active, Closed
- Drag cards to reorder within status group
- Click card to open job conversation
- Jobs visible regardless of active channel

#### FR-JOBS-005: Job persistence
- JSON file persistence (`jobs.json`)
- Survives server restarts

### 6.8 Rules System

#### FR-RULES-001: Rule lifecycle
- Rules set working style for agents
- States: `pending` -> `active` / `draft` / `archived`
- Max 160 characters per rule
- Max 10 active rules, soft warning at 7

#### FR-RULES-002: Rule management
- **Agent proposal**: Via MCP `chat_rules(action='propose')` — proposal card appears in timeline
- **Human actions on proposal**: Activate, Add to drafts, or Dismiss
- **Human direct creation**: `+` button in Rules panel
- **Rules panel**: Groups: Active, Drafts, Archive. Drag between groups. Inline editing.
- **Delete**: Drag archived rules to trash

#### FR-RULES-003: Rule delivery to agents
- Active rules sent to agents on next trigger
- Re-sent when rules change or according to Rule refresh setting
- "Remind agents" button re-sends on next trigger
- Epoch-based invalidation: epoch bumps on any active-set change
- Per-agent sync tracking (last-seen epoch)

#### FR-RULES-004: Rule refresh settings
- Configurable: 0 = on change only, or every 5/10/20/50 triggers
- Badge on Rules button shows unseen proposals only

#### FR-RULES-005: Rule persistence
- JSON file persistence (`rules.json`) with epoch tracking
- Survives server restarts

### 6.9 Sessions

#### FR-SESS-001: Session concept
- Structured multi-agent workflows with sequential phases, role casting, and turn-taking
- Channel-scoped (one active session per channel)
- Survive page refreshes

#### FR-SESS-002: Built-in templates
| Template | Description |
|----------|-------------|
| Code Review | Submit code, review, red-team, synthesize |
| Debate | Opening arguments, rebuttals, synthesis |
| Design Critique | Present design, critique, iterate |
| Planning | Requirements gathering, planning, review |

Each template defines: id, name, description, roles[], phases[] (with name, participants, prompt, is_output)

#### FR-SESS-003: Session launch flow
1. Click play button in input area to open launcher
2. Pick a template
3. Review auto-cast (agent -> role assignment)
4. Adjust casting if needed
5. Start session

#### FR-SESS-004: Custom sessions
1. Click "Design a session" in launcher
2. Pick an agent and describe desired workflow
3. Agent proposes session draft as card in timeline
4. Actions: **Run** (opens cast preview -> start), **Save Template** (reusable), **Request Changes** (inline feedback), **Dismiss**

#### FR-SESS-005: Session execution
- Phase banners mark transitions in timeline
- Sticky session bar shows progress (current phase, waiting agent, end button)
- Agents triggered sequentially with phase-specific prompts
- Dissent mandate for review/critique roles
- States: `active` -> `waiting` -> `complete` / `interrupted` / `paused`
- Auto-advance phases when expected agent responds
- Output phase highlighted when session completes

#### FR-SESS-006: Session persistence
- Session runs persisted to `session_runs.json`
- Custom templates persisted to `custom_templates.json`
- Built-in templates loaded from `session_templates/` directory

### 6.10 Scheduled Messages

#### FR-SCHED-001: Schedule creation
- Split send button with clock icon to open schedule popover
- One-shot: pick date/time
- Recurring: check "Recurring" and set interval (minutes, hours, or days)
- Validates at least one agent is toggled before enabling Schedule button

#### FR-SCHED-002: Schedule execution
- Scheduled messages fire as real chat messages from the human user
- Complete with @mentions that trigger agents automatically
- Server checks every 30 seconds for due schedules

#### FR-SCHED-003: Schedule management
- Schedule strip above composer shows active/paused schedules
- Single schedule: inline pause and delete controls
- Multiple schedules: expandable strip to manage
- Pause/resume toggle
- Delete schedule

#### FR-SCHED-004: Schedule persistence
- JSON persistence (`schedules.json`)
- Supports intervals (minutes/hours/days) and daily-at-time patterns
- Survives server restarts

### 6.11 API Agents (Local Models)

#### FR-API-001: OpenAI-compatible API integration
- Connect any local model with OpenAI-compatible API (Ollama, llama-server, LM Studio, vLLM)
- Agents get status pills, activity indicators, @mention routing, multi-instance support

#### FR-API-002: API agent configuration
In `config.local.toml` (gitignored):
```toml
[agents.qwen]
type = "api"
base_url = "http://localhost:8189/v1"
model = "qwen3-4b"
color = "#8b5cf6"
label = "Qwen"
# api_key_env = "OPENAI_API_KEY"  # optional
```

#### FR-API-003: API agent wrapper (`wrapper_api.py`)
- Registers with server, watches for @mentions
- Reads recent chat context on trigger
- Calls model's `/v1/chat/completions` endpoint
- Posts response back to chat
- Heartbeat and presence management same as CLI agents

### 6.12 Auxiliary Features

#### FR-AUX-001: Pinned messages
- Hover message -> click pin button
- Cycle: not pinned -> todo (purple strip) -> done (green strip) -> cleared
- Pins panel (pin icon in header) shows all pinned items
- Open items on top, done items below with strikethrough
- Persisted in `todos.json`

#### FR-AUX-002: Image sharing
- Paste or drag-and-drop images in web UI
- Agents attach local images via MCP (`chat_send` with `image_path`)
- Images render inline in messages
- Click to open in lightbox modal
- Upload validation: png/jpg/gif/webp/bmp/svg, max 10MB
- Stored in `uploads/` directory

#### FR-AUX-003: Voice typing
- Mic button (Chrome/Edge Web Speech API)
- Dictate messages instead of typing
- Real-time transcription into input field

#### FR-AUX-004: Channel summaries
- Per-channel text snapshots for agent catch-up
- Agents: `chat_summary(action='read')` at session start
- Written by agents (self-initiated or human-triggered via `/summary @agent`)
- 1000-character cap
- Persisted in `summaries.json`

#### FR-AUX-005: Slash commands
| Command | Behavior |
|---------|----------|
| `/clear` | Clear messages in current channel |
| `/continue` | Resume after loop guard pause |
| `/summary @agent` | Ask agent to summarize current channel |
| `/hatmaking` | All agents design SVG hats for avatars |
| `/artchallenge [theme]` | SVG art challenge |
| `/roastreview` | Agents review/roast each other |
| `/poetry haiku\|limerick\|sonnet` | Agents write poetry |

- Type `/` in input to open Slack-style autocomplete menu

#### FR-AUX-006: Notification sounds
- Per-agent notification sounds when message arrives while window unfocused
- 7 built-in sounds + "None" option
- Configurable in Settings per agent
- Silent during history load, for join/leave events, and own messages

#### FR-AUX-007: Unread indicators
- Channel tabs show unread counts
- Scroll-to-bottom arrow displays unread badge
- Rules panel badge shows unseen proposals

#### FR-AUX-008: Agent hats
- SVG overlays on agent avatars (viewBox `0 0 32 16`, max 5KB)
- Set via `chat_set_hat` MCP tool or `/hatmaking` command
- Persist across page reloads (stored in `hats.json`)
- Drag hat to trash icon to remove

#### FR-AUX-009: Agent roles
- Assign roles: Planner, Builder, Reviewer, Researcher, or custom
- Role pill in message header -> click to open picker
- Roles are global per agent (not per-channel)
- Persist in `roles.json`
- Wrapper appends role to injected prompt

#### FR-AUX-010: Update notification
- Version check on page load (30-minute server-side cache)
- Small update pill in channel bar when newer release available
- Links to releases page, dismissible
- Forks see "Upstream update available"

#### FR-AUX-011: File path clicking
- Clickable file paths in messages
- Opens in native file manager (Explorer/Finder/file manager)
- POST `/api/open-path` endpoint

---

## 7. Non-Functional Requirements

### NFR-001: Performance
- WebSocket message delivery: < 100ms latency on localhost
- Support 10+ concurrent agent instances
- File-based persistence must handle 100,000+ messages without degradation
- Schedule check interval: 30 seconds

### NFR-002: Reliability
- All data persisted to disk (survive server restarts)
- Atomic file writes (temp file + rename) for JSON stores
- JSONL with `fsync()` for message durability
- Monotonically increasing message IDs survive deletions
- Agent crash recovery: 60s timeout auto-deregisters, grace period for name reservation

### NFR-003: Cross-Platform
- Server: Windows, macOS, Linux
- Agent wrapper: Windows (Win32 API), macOS/Linux (tmux)
- Browser UI: Chrome, Edge, Firefox, Safari (desktop)
- Web Speech API: Chrome/Edge only (voice typing)

### NFR-004: Token Efficiency
- Overhead per `chat_read`: ~30 + 40 tokens per message (tool invocation + JSON wrapper)
- Overhead per `chat_send`: ~45 tokens
- Tool definitions in system prompt: ~850 input tokens (one-time)
- Cursor-based reads return only new messages
- Loop guard prevents runaway token consumption

### NFR-005: Usability
- Dark-themed UI optimized for developer workflow
- Single-page app, no page reloads needed
- Auto-saving settings
- One-click agent launchers (platform-specific scripts)
- First-launch auto-setup (venv, dependencies, MCP config)

### NFR-006: Maintainability
- Modular Python backend (separate files per concern)
- Modular frontend (separate JS files per feature area)
- Pub/Sub pattern for cross-module communication
- TOML-based configuration

---

## 8. Data Models

### 8.1 Message

```json
{
  "id": 42,
  "sender": "claude",
  "text": "Hello! I've reviewed the code...",
  "type": "chat",
  "timestamp": 1710000000.0,
  "time": "14:30:00",
  "channel": "general",
  "reply_to": 38,
  "attachments": [
    {"name": "screenshot.png", "url": "/uploads/abc123.png"}
  ],
  "metadata": {
    "rule_text": "...",
    "job_title": "..."
  }
}
```

**Storage:** JSONL file (`data/mehub_log.jsonl`), one JSON object per line.

### 8.2 Agent Instance

```json
{
  "name": "claude",
  "base": "claude",
  "slot": 0,
  "label": "Claude",
  "color": "#a78bfa",
  "identity_id": "uuid-string",
  "token": "bearer-token",
  "epoch": 1710000000.0,
  "state": "active",
  "registered_at": 1710000000.0
}
```

**Storage:** In-memory `RuntimeRegistry` seeded from config. Renames persisted in `data/renames.json`.

### 8.3 Job

```json
{
  "id": "job-uuid",
  "type": "task",
  "title": "Fix rendering bug",
  "body": "The renderer crashes when...",
  "status": "open",
  "channel": "general",
  "created_by": "claude",
  "assignee": "codex",
  "messages": [
    {
      "id": 0,
      "sender": "claude",
      "text": "I've identified the issue...",
      "time": "14:30:00",
      "timestamp": 1710000000.0,
      "type": "chat",
      "deleted": false
    }
  ],
  "sort_order": 0
}
```

**Storage:** JSON file (`data/jobs.json`)

### 8.4 Rule

```json
{
  "id": "rule-uuid",
  "text": "Always write tests before implementation",
  "reason": "Ensures code quality",
  "status": "active",
  "proposed_by": "claude",
  "created_at": 1710000000.0
}
```

**Storage:** JSON file (`data/rules.json`) with top-level `epoch` counter.

### 8.5 Schedule

```json
{
  "id": "sched-uuid",
  "text": "@claude check the build status",
  "channel": "general",
  "created_by": "You",
  "one_shot_at": "2026-03-12T15:00:00",
  "interval_seconds": null,
  "interval_label": null,
  "paused": false,
  "last_fired": null,
  "next_fire": "2026-03-12T15:00:00"
}
```

**Storage:** JSON file (`data/schedules.json`)

### 8.6 Session Run

```json
{
  "id": "sess-uuid",
  "template_id": "code-review",
  "channel": "general",
  "cast": {"builder": "claude", "reviewer": "codex"},
  "state": "active",
  "current_phase": 0,
  "current_turn": 0,
  "started_at": 1710000000.0,
  "ended_at": null
}
```

**Storage:** JSON file (`data/session_runs.json`)

### 8.7 Session Template

```json
{
  "id": "code-review",
  "name": "Code Review",
  "description": "Structured code review with multiple perspectives",
  "roles": ["builder", "reviewer", "red_team", "synthesiser"],
  "phases": [
    {
      "name": "Submit",
      "participants": ["builder"],
      "prompt": "Present the code to review...",
      "is_output": false
    },
    {
      "name": "Review",
      "participants": ["reviewer", "red_team"],
      "prompt": "Review the submitted code...",
      "is_output": false
    },
    {
      "name": "Synthesis",
      "participants": ["synthesiser"],
      "prompt": "Synthesize findings...",
      "is_output": true
    }
  ]
}
```

**Storage:** Built-in: `session_templates/*.json`. Custom: `data/custom_templates.json`.

### 8.8 Summary

```json
{
  "general": {
    "text": "Discussion about refactoring the renderer...",
    "author": "claude",
    "timestamp": 1710000000.0,
    "message_id": 42
  }
}
```

**Storage:** JSON file (`data/summaries.json`). 1000-char cap per summary.

### 8.9 Pin/Todo

```json
{
  "42": "todo",
  "38": "done"
}
```

**Storage:** JSON file (`data/todos.json`). Keys are message IDs, values are states.

### 8.10 Room Settings

```json
{
  "title": "mehub",
  "username": "You",
  "font": "mono",
  "channels": ["general"],
  "history_limit": "all",
  "contrast": "normal",
  "max_agent_hops": 4,
  "rules_refresh_interval": 0
}
```

**Storage:** JSON file (`data/settings.json`)

---

## 9. API Specification

### 9.1 REST Endpoints

#### Public (no auth)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve index.html with injected session token |
| GET | `/static/*` | Static assets (JS, CSS, images, sounds) |
| GET | `/uploads/{filename}` | Uploaded images (path traversal protected) |
| GET | `/api/roles` | All agent roles |

#### Token-authenticated (session token in header/query)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/messages` | Messages (params: since_id, limit, channel) |
| POST | `/api/upload` | Upload image (max 10MB) |
| GET | `/api/status` | Agent status (online/busy/role) |
| GET | `/api/settings` | Room settings |
| DELETE | `/api/hat/{agent_name}` | Remove agent hat |
| POST | `/api/roles/{agent_name}` | Set/clear agent role |
| GET | `/api/rules` | All rules (all states) |
| GET | `/api/rules/active` | Active rules + epoch |
| POST | `/api/rules/remind` | Bump epoch to resend rules |
| POST | `/api/rules/agent_sync/{name}` | Agent reports rules epoch |
| GET | `/api/rules/freshness` | Per-agent rule sync status |
| POST | `/api/label/{name}` | Rename/relabel agent |
| GET | `/api/platform` | Server platform |
| POST | `/api/open-path` | Open file in native file manager |
| GET | `/api/jobs` | List jobs (params: channel, status) |
| POST | `/api/jobs` | Create job |
| PATCH | `/api/jobs/{id}` | Update job |
| POST | `/api/jobs/reorder` | Reorder jobs |
| GET | `/api/jobs/{id}/messages` | Job thread messages |
| POST | `/api/jobs/{id}/messages` | Post to job thread |
| DELETE | `/api/jobs/{id}/messages/{msg_id}` | Soft-delete job message |
| POST | `/api/jobs/{id}/messages/{idx}/resolve` | Accept/dismiss suggestion |
| DELETE | `/api/jobs/{id}` | Delete/archive job |
| GET | `/api/schedules` | List schedules |
| POST | `/api/schedules` | Create schedule |
| DELETE | `/api/schedules/{id}` | Delete schedule |
| PATCH | `/api/schedules/{id}/toggle` | Pause/resume schedule |
| POST | `/api/trigger-agent` | Silent agent trigger |
| POST | `/api/messages/{id}/demote` | Demote proposal |
| POST | `/api/messages/{id}/resolve_rule_proposal` | Handle rule proposal |
| POST | `/api/messages/{id}/demote_rule_proposal` | Demote rule proposal |
| GET | `/api/sessions/templates` | List session templates |
| GET | `/api/sessions/active` | Active session for channel |
| GET | `/api/sessions/active-all` | All active sessions |
| POST | `/api/sessions/start` | Start session |
| POST | `/api/sessions/{id}/end` | End session |
| POST | `/api/sessions/request-draft` | Request session design |
| POST | `/api/sessions/save-draft` | Save as template |
| DELETE | `/api/sessions/templates/{id}` | Delete custom template |
| GET | `/api/version_check` | Check for updates (30-min cache) |

#### Loopback-only (127.0.0.1 / ::1)
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/register` | None | Register new agent instance |
| POST | `/api/deregister/{name}` | Bearer | Deregister agent |
| POST | `/api/heartbeat/{name}` | Bearer | Presence + activity heartbeat |

#### Bearer-authenticated (agent instance token)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/send` | REST send for API agents |

### 9.2 MCP Endpoints
| Transport | URL | Purpose |
|-----------|-----|---------|
| HTTP (streamable) | `http://127.0.0.1:{mcp.http_port}/mcp` | Claude Code, Codex |
| SSE | `http://127.0.0.1:{mcp.sse_port}/sse` | Gemini CLI |

---

## 10. WebSocket Protocol

### Connection
```
ws://localhost:8300/ws?token={session_token}
```
Invalid token results in close code 4003 (triggers client auto-reload).

### Initial State (server -> client on connect)
Server sends these events in sequence:
1. `settings` — Room settings
2. `agents` — Registered agent instances
3. `base_colors` — Config-defined agent colors
4. `todos` — Pinned message states
5. `rules` — All rules
6. `hats` — Agent hat SVGs
7. `jobs` — All jobs
8. `schedules` — All schedules
9. `pending_instance` — For each pending agent (naming lightbox)
10. `message` — History messages per channel
11. `status` — Agent online/busy/role status

### Client -> Server Events

| Type | Payload | Description |
|------|---------|-------------|
| `message` | `{text, attachments, reply_to, channel}` | Send chat message |
| `delete` | `{ids: [int]}` | Delete messages |
| `todo_add` | `{id: int}` | Pin a message |
| `todo_toggle` | `{id: int}` | Toggle todo/done |
| `todo_remove` | `{id: int}` | Unpin message |
| `rule_propose` | `{text, reason}` | Propose new rule |
| `rule_activate` | `{id}` | Activate rule |
| `rule_deactivate` | `{id}` | Archive rule |
| `rule_make_draft` | `{id}` | Move to drafts |
| `rule_edit` | `{id, text, reason}` | Edit rule |
| `rule_delete` | `{id}` | Delete rule |
| `rule_remind` | `{}` | Resend rules |
| `update_settings` | `{key: value, ...}` | Update settings |
| `rename_agent` | `{old_name, new_name}` | Rename agent |
| `name_pending` | `{identity_id, name}` | Name pending instance |
| `channel_create` | `{name}` | Create channel |
| `channel_rename` | `{old_name, new_name}` | Rename channel |
| `channel_delete` | `{name}` | Delete channel |

### Server -> Client Events

| Type | Payload | Description |
|------|---------|-------------|
| `message` | Message object | New message |
| `status` | `{name, online, busy, role}` | Agent status change |
| `typing` | `{sender}` | Agent typing |
| `clear` | `{channel}` | Channel cleared |
| `todo_update` | `{id, state}` | Pin state changed |
| `settings` | Settings object | Settings changed |
| `rule` | Rule object | Rule CRUD |
| `rules_remind` | `{}` | Rules resent |
| `agents` | Agent list | Agent config updated |
| `base_colors` | Color map | Agent colors |
| `hats` | Hat map | Hat SVGs updated |
| `job` | Job event | Job CRUD/message |
| `schedule` | Schedule event | Schedule CRUD |
| `session` | Session event | Session lifecycle |
| `delete` | `{ids: [int]}` | Messages deleted |
| `edit` | `{id, metadata}` | Message metadata updated |
| `agent_renamed` | `{old, new, label, color}` | Agent renamed |
| `channel_renamed` | `{old_name, new_name}` | Channel renamed |
| `pending_instance` | Instance info | New agent needs naming |
| `todos` | Full state map | All pins (on connect) |
| `rules` | Full rules list | All rules (on connect) |
| `jobs` | Full jobs list | All jobs (on connect) |
| `schedules` | Full schedule list | All schedules (on connect) |

---

## 11. MCP Tools Specification

### chat_send
Send a message to a channel or job thread.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sender` | string | Yes (injected by proxy) | Sender identity |
| `message` | string | Yes | Message text (markdown) |
| `image_path` | string | No | Local file path to attach as image |
| `reply_to` | int | No | Message ID to reply to |
| `channel` | string | No | Target channel (default: "general") |
| `job_id` | string | No | Post to job thread instead |

### chat_read
Read messages from a channel or job thread, with auto-cursor.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sender` | string | Yes (injected) | Reader identity |
| `since_id` | int | No | Override cursor (read from this ID) |
| `limit` | int | No | Max messages to return |
| `channel` | string | No | Channel to read (omit for all) |
| `job_id` | string | No | Read job thread instead |

**Returns:** Array of messages since last read cursor. Includes identity breadcrumbs for multi-instance.

### chat_resync
Full context refresh — resets cursor.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sender` | string | Yes (injected) | Reader identity |
| `limit` | int | No | Max messages |
| `channel` | string | No | Channel to resync |

### chat_join
Announce presence in a channel.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes (injected) | Agent name |
| `channel` | string | No | Channel to join (default: general) |

### chat_who
List all online agents.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| (none) | | | Returns list of online agent names, labels, roles |

### chat_rules
List active rules or propose a new rule.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | "list" or "propose" |
| `sender` | string | Yes (injected) | Agent name |
| `rule` | string | Conditional | Rule text (required for propose) |
| `reason` | string | No | Reason for proposing |
| `channel` | string | No | Channel for proposal message |

### chat_channels
List available channels.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| (none) | | | Returns list of channel names |

### chat_set_hat
Set an SVG hat overlay on an agent's avatar.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sender` | string | Yes (injected) | Agent setting the hat |
| `svg` | string | Yes | SVG content (viewBox 0 0 32 16, max 5KB) |
| `target` | string | No | Target agent (default: self) |

### chat_claim
Claim or reclaim an identity in multi-instance setup.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sender` | string | Yes (injected) | Current identity |
| `name` | string | Yes | Desired name to claim |

### chat_summary
Read or write channel summaries.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | "read" or "write" |
| `sender` | string | Yes (injected) | Agent name |
| `text` | string | Conditional | Summary text (required for write, max 1000 chars) |
| `channel` | string | No | Channel (default: general) |

### chat_propose_job
Propose a job for human approval.
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `sender` | string | Yes (injected) | Proposing agent |
| `title` | string | Yes | Job title |
| `body` | string | Yes | Job description |
| `channel` | string | No | Channel for proposal card |

---

## 12. Frontend Specification

### 12.1 Architecture

Single-page application with modular vanilla JavaScript.

| Module | File | Responsibility |
|--------|------|---------------|
| Event Hub | `core.js` | Pub/sub for inter-module communication |
| State Store | `store.js` | Minimal reactive state (get/set/watch) |
| Main Client | `chat.js` | WebSocket, message rendering, settings, @mention autocomplete, voice, images, sounds, avatars |
| Channels | `channels.js` | Channel tabs, switching, create/rename/delete |
| Jobs | `jobs.js` | Jobs panel, conversation view, drag-and-drop, suggestion cards |
| Sessions | `sessions.js` | Launcher, draft cards, cast assignment, template management |
| Rules Panel | `rules-panel.js` | Rules sidebar, drag-and-drop, inline editing, badges |

### 12.2 Design System

- **Theme:** Dark background (#1a1a2e base)
- **Font options:** Monospace (default) or Sans-serif
- **High contrast mode:** Available in settings
- **Agent colors:** Configurable per agent in config.toml, with HSL shift for multi-instance
- **Colored @mention pills:** Slack-style inline mentions
- **Status pills:** Animated spinning border when agent is working

### 12.3 Key UI Components

1. **Header bar**: Logo, title, agent status pills (click to rename), jobs/rules/pins/settings toggle buttons
2. **Settings bar**: Collapsible row — username, font, loop guard, history limit, contrast, rule refresh, notification sounds
3. **Channel bar**: Tab row with unread badges, `+` create, edit/delete on active tab, update notification pill
4. **Session bar**: Sticky progress bar during active session (phase name, waiting agent, end button)
5. **Timeline**: Main message area with auto-scroll, date dividers, pin strips, delete mode overlay
6. **Jobs panel**: Resizable sidebar with kanban columns
7. **Rules panel**: Resizable sidebar with drag-and-drop groups
8. **Pins panel**: Collapsible sidebar with open/done grouping
9. **Input area**: Text input with @mention toggles, slash autocomplete, reply preview, schedule popover, voice button, send/schedule split button
10. **Schedules bar**: Strip above composer showing active schedules
11. **Lightbox**: Image viewer modal
12. **Instance naming lightbox**: Modal for naming multi-instance agents

### 12.4 Rendering Pipeline

1. WebSocket `message` event received
2. Check `window._messageRenderers[msg.type]` for custom renderer
3. Apply markdown rendering (marked.js) with syntax highlighting (highlight.js)
4. Generate agent avatar with color and optional hat SVG overlay
5. Add pin strip, reply quote, copy button, delete button
6. Append to timeline with smooth scroll

### 12.5 Client-side Storage (localStorage)
| Key | Purpose |
|-----|---------|
| `mehub-channel` | Last active channel |
| `mehub-sounds` | Per-agent notification sound preferences |
| `mehub-dismissed-version` | Dismissed update notification version |

---

## 13. Configuration

### 13.1 `config.toml` (main config, tracked in git)

```toml
[server]
port = 8300                    # Web UI port
host = "127.0.0.1"            # Bind address (localhost only)
data_dir = "./data"            # Data directory

[agents.claude]
command = "claude"             # CLI command (must be on PATH)
cwd = ".."                     # Working directory for agent
color = "#da7756"              # Status pill + @mention color
label = "Claude"               # Display name
# mcp_inject = "flag"          # How to inject MCP config
# mcp_flag = "--mcp-config"    # CLI flag for config file path
# mcp_transport = "http"       # "http" or "sse"

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
color = "#22d3ee"
label = "Kimi"

[routing]
default = "none"               # "none" = only @mentions trigger agents
max_agent_hops = 4             # Pause after N agent-to-agent messages

[mcp]
http_port = 8200               # MCP streamable-http port
sse_port = 8201                # MCP SSE transport port

[images]
upload_dir = "./uploads"       # Image upload directory
max_size_mb = 10               # Max upload size
```

### 13.2 `config.local.toml` (local overrides, gitignored)

Used primarily for API agents (local models):
```toml
[agents.qwen]
type = "api"
base_url = "http://localhost:8189/v1"
model = "qwen3-4b"
color = "#8b5cf6"
label = "Qwen"
# api_key_env = "OPENAI_API_KEY"
```

**Merge behavior:** Only `[agents]` section merged from local config. Existing agents from main config are NOT overridden.

### 13.3 Room Settings (UI-editable, persisted)

| Setting | Default | Description |
|---------|---------|-------------|
| `title` | "mehub" | Room title |
| `username` | "You" | Human display name |
| `font` | "mono" | "mono" or "sans" |
| `channels` | ["general"] | Channel list |
| `history_limit` | "all" | "all" or 25-10000 |
| `contrast` | "normal" | "normal" or "high" |
| `max_agent_hops` | 4 | Loop guard threshold (1-50) |
| `rules_refresh_interval` | 0 | 0 = on change only, or 5/10/20/50 triggers |

---

## 14. Security Requirements

### SEC-001: Session token
- Random 32-byte hex generated on each server start
- Injected into web UI HTML via same-origin script tag
- Required for all API and WebSocket requests
- Displayed in terminal on startup (accessible only to local processes)

### SEC-002: Bearer authentication
- Each registered agent gets a unique per-instance token
- Used for MCP tool calls and REST API
- Tokens are ephemeral (regenerated on each agent registration)

### SEC-003: Loopback-only registration
- `/api/register`, `/api/deregister`, `/api/heartbeat` accept only from `127.0.0.1` / `::1`
- Prevents remote agent impersonation

### SEC-004: Origin checking
- Reject requests with `Origin` header not matching `localhost:{port}` / `127.0.0.1:{port}`
- Prevents cross-origin and DNS rebinding attacks

### SEC-005: SVG sanitization
- Strip `<script>`, `on*=` event handlers, `javascript:` URIs from hat SVGs
- Prevents XSS via agent-uploaded SVGs

### SEC-006: Upload validation
- Extension whitelist: png, jpg, jpeg, gif, webp, bmp, svg
- Size limit: configurable (default 10MB)
- Path traversal protection: `is_relative_to()` check on file serving

### SEC-007: No shell injection
- All subprocess calls use argument lists (no `shell=True`)
- Prevents shell injection attacks

### SEC-008: Network binding protection
- Refuses to start on non-localhost address unless `--allow-network` flag is explicitly passed
- Requires confirmation (`YES`) when network mode is used
- Warning about LAN exposure risks

### SEC-009: WebSocket validation
- Invalid session token on WS connect results in close code 4003
- Client auto-reloads on 4003 to get fresh token

### SEC-010: Agent identity enforcement
- Agents must use their authenticated identity
- Stale/invalid names are rejected
- MCP proxy enforces sender identity on all tool calls

---

## 15. File Structure

```
mehub/
├── run.py                    # Entry point — starts MCP + web server
├── app.py                    # FastAPI server, REST endpoints, WebSocket, security
├── store.py                  # JSONL message persistence with observer callbacks
├── registry.py               # Runtime agent registry — slot assignment, identity, rename
├── router.py                 # @mention parsing, agent routing, loop guard
├── agents.py                 # File-based trigger queue for wrapper
├── mcp_bridge.py             # MCP tool definitions (chat_send, chat_read, etc.)
├── mcp_proxy.py              # Per-instance MCP proxy — identity injection
├── wrapper.py                # Cross-platform agent lifecycle dispatcher
├── wrapper_windows.py        # Windows: keystroke injection + screen buffer detection
├── wrapper_unix.py           # macOS/Linux: tmux injection + pane capture
├── wrapper_api.py            # API agent wrapper (OpenAI-compatible endpoints)
├── jobs.py                   # Job store — persistence, status, threaded conversations
├── rules.py                  # Rule store — propose/activate/draft/archive with epochs
├── schedules.py              # Schedule store — one-shot/recurring, interval parsing
├── summaries.py              # Per-channel summary store (1000-char cap)
├── session_engine.py         # Session orchestration — phases, turns, prompts
├── session_store.py          # Session persistence — runs, templates
├── config_loader.py          # TOML config merge (main + local)
├── config.toml               # Main configuration
├── config.local.toml.example # Local config template (gitignored when active)
├── requirements.txt          # Python dependencies
├── VERSION                   # Version string
├── LICENSE                   # MIT license
│
├── static/                   # Frontend assets
│   ├── index.html            # Single-page app shell
│   ├── style.css             # Main dark theme stylesheet
│   ├── sessions.css          # Session-specific styles
│   ├── jobs.css              # Jobs-specific styles
│   ├── core.js               # EventHub (pub/sub)
│   ├── store.js              # Reactive state store
│   ├── chat.js               # Main client (WebSocket, rendering, input)
│   ├── channels.js           # Channel management
│   ├── jobs.js               # Jobs panel
│   ├── sessions.js           # Session launcher & management
│   ├── rules-panel.js        # Rules sidebar
│   ├── sounds/               # 7 notification MP3s
│   ├── logo.png              # App logo
│   └── favicon.ico           # Favicon
│
├── session_templates/        # Built-in session templates
│   ├── code-review.json
│   ├── debate.json
│   ├── design-critique.json
│   └── planning.json
│
├── data/                     # Persistent data (auto-created)
│   ├── mehub_log.jsonl       # Message log
│   ├── settings.json         # Room settings
│   ├── todos.json            # Pinned messages
│   ├── rules.json            # Rules with epoch
│   ├── jobs.json             # Jobs
│   ├── schedules.json        # Schedules
│   ├── summaries.json        # Channel summaries
│   ├── session_runs.json     # Session state
│   ├── custom_templates.json # Custom session templates
│   ├── hats.json             # Agent hat SVGs
│   ├── roles.json            # Agent roles
│   ├── renames.json          # Agent rename history
│   ├── mcp_cursors.json      # MCP read cursors
│   └── {name}_queue.jsonl    # Per-agent trigger queues
│
├── uploads/                  # Uploaded images (auto-created)
│
├── windows/                  # Windows launcher scripts
│   ├── start.bat
│   ├── start_claude.bat
│   ├── start_codex.bat
│   ├── start_gemini.bat
│   ├── start_kimi.bat
│   ├── start_claude_skip-permissions.bat
│   ├── start_codex_bypass.bat
│   ├── start_gemini_yolo.bat
│   ├── start_api_agent.bat
│   └── open_chat.html
│
└── macos-linux/              # macOS/Linux launcher scripts
    ├── start.sh
    ├── start_claude.sh
    ├── start_codex.sh
    ├── start_gemini.sh
    ├── start_kimi.sh
    ├── start_claude_skip-permissions.sh
    ├── start_codex_bypass.sh
    ├── start_gemini_yolo.sh
    └── start_api_agent.sh
```

---

## 16. Dependencies

### Python Packages (`requirements.txt`)

| Package | Purpose |
|---------|---------|
| `fastapi>=0.110` | Web framework (REST + WebSocket) |
| `uvicorn[standard]>=0.29` | ASGI server |
| `mcp>=1.0` | MCP SDK (FastMCP) |

### System Dependencies

| Dependency | Platform | Purpose |
|-----------|----------|---------|
| Python 3.11+ | All | Runtime |
| tmux | macOS/Linux | Agent terminal sessions |
| At least one AI agent CLI | All | Claude Code / Codex / Gemini / Kimi |

### Frontend (CDN)

| Library | Purpose |
|---------|---------|
| marked.js | Markdown rendering |
| highlight.js | Code syntax highlighting |

---

## 17. Development Phases

### Phase 1: Core Infrastructure (Week 1-2)
- [ ] Project setup, config loader, TOML parsing
- [ ] FastAPI server with WebSocket support
- [ ] Session token generation and security middleware
- [ ] Message store (JSONL persistence)
- [ ] Basic REST endpoints (messages, settings, upload)
- [ ] Basic web UI (dark theme, message rendering, input)

### Phase 2: Agent Integration (Week 2-3)
- [ ] Agent registry (registration, heartbeat, deregistration)
- [ ] MCP bridge with dual transport (HTTP + SSE)
- [ ] MCP proxy (per-instance identity injection)
- [ ] Agent wrapper (cross-platform dispatcher)
- [ ] Platform-specific wrappers (Win32 + tmux)
- [ ] File-based trigger queue system
- [ ] Activity monitoring

### Phase 3: Communication Features (Week 3-4)
- [ ] @mention routing and parsing
- [ ] Loop guard (per-channel, configurable)
- [ ] Channel system (create, rename, delete, switch)
- [ ] @mention autocomplete UI
- [ ] Reply threading
- [ ] Unread indicators

### Phase 4: Agent Coordination (Week 4-5)
- [ ] Multi-instance agent support
- [ ] Instance naming lightbox
- [ ] Color shifting for multi-instance
- [ ] Identity claim system
- [ ] Agent roles (assign, persist, inject into prompts)
- [ ] API agent wrapper for local models

### Phase 5: Workflow Features (Week 5-6)
- [ ] Rules system (propose, activate, draft, archive, epoch tracking)
- [ ] Rules panel UI with drag-and-drop
- [ ] Jobs system (create, lifecycle, threaded conversations)
- [ ] Jobs panel UI with kanban
- [ ] Job proposals from agents

### Phase 6: Advanced Features (Week 6-7)
- [ ] Sessions (engine, store, templates)
- [ ] Session launcher UI with cast assignment
- [ ] Custom session design flow
- [ ] Scheduled messages (one-shot, recurring)
- [ ] Schedule UI (popover, strip)

### Phase 7: Polish & Auxiliary (Week 7-8)
- [ ] Pinned messages UI
- [ ] Image sharing (paste, drag-drop, lightbox)
- [ ] Voice typing
- [ ] Channel summaries
- [ ] Slash commands (all)
- [ ] Notification sounds
- [ ] Agent hat system
- [ ] Message deletion UI
- [ ] Update notification check
- [ ] File path clicking
- [ ] Launcher scripts (Windows + macOS/Linux)

### Phase 8: Testing & Launch (Week 8-9)
- [ ] Cross-platform testing (Windows, macOS, Linux)
- [ ] Multi-agent scenario testing
- [ ] Security audit (origin checking, path traversal, injection)
- [ ] Performance testing (large message volumes)
- [ ] Documentation (README, configuration guide)
- [ ] Release packaging

---

## 18. Open Questions

_None at this time. This PRD is a 1:1 clone specification of agentchattr v0.2.1, branded as mehub._

---

> **Note:** This PRD is derived from a comprehensive analysis of the agentchattr v0.2.1 source code. All functional requirements, data models, API specifications, and architectural details reflect the actual implementation. Deviations during development should be documented as amendments to this PRD.
