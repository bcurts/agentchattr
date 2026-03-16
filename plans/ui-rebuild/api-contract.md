# API Contract Documentation (Corrected)

**Date:** 2026-03-14 (Updated)
**Purpose:** Document all REST endpoints and WebSocket events that the new UI must support
**Status:** ✅ Corrected per actual backend code (`app.py`, `jobs.py`, etc.)
**Frozen:** YES — No changes allowed during UI rebuild without separate backend PR

---

## REST Endpoints (46 total actual)

### WebSocket
- **Path:** `/ws`
- **Type:** WebSocket upgrade
- **Auth:** Session token via `X-Session-Token` header
- **On Connect:** Server sends snapshot events (see WebSocket section)

### Messages

#### GET `/api/messages`
- **Purpose:** Fetch message history
- **Auth:** Session token
- **Query:** `since_id?: int`, `limit?: int (default 50)`, `channel?: str (default '')`
- **Response (200):** Direct array: `Message[]`
  - Fields: `id: int`, `sender: str`, `text: str`, `timestamp: int`, `channel: str`, `reply_to?: int`, `pinned?: bool`, `todo_state?: 'open'|'done'`, `marked_for_rule?: bool`

#### POST `/api/send`
- **Purpose:** Send a new message
- **Auth:** Session token (via `X-Session-Token`)
- **Request Body:** `{ text: str, channel?: str, reply_to?: int }`
- **Response (200):** `Message` (single object, not wrapped)

#### POST `/api/messages/{msg_id}/demote`
- **Purpose:** Undo pinning (demote message from pinned)
- **Auth:** Session token
- **Request:** None
- **Response (200):** `{ ok: bool }`

#### POST `/api/messages/{msg_id}/resolve_rule_proposal`
- **Purpose:** Accept/reject a proposed rule (from message)
- **Auth:** Session token
- **Request:** `{ activate: bool }`
- **Response (200):** `{ ok: bool }`

#### POST `/api/messages/{msg_id}/demote_rule_proposal`
- **Purpose:** Demote/reject a proposed rule
- **Auth:** Session token
- **Request:** None
- **Response (200):** `{ ok: bool }`

---

### File Upload

#### POST `/api/upload`
- **Purpose:** Upload file (image, document)
- **Auth:** Session token
- **Request:** Multipart form-data (key: `file`)
- **Response (200):** `{ filename: str, url: str }`

#### GET `/uploads/{filename}`
- **Purpose:** Download uploaded file
- **Auth:** Session token
- **Response (200):** File binary

---

### Jobs & Kanban Board

#### GET `/api/jobs`
- **Purpose:** Get all jobs
- **Auth:** Session token
- **Query:** `channel?: str`, `status?: str (open|done|archived)`
- **Response (200):** Direct array: `Job[]`
  - Fields: `id: int`, `title: str`, `created_by: str`, `status: 'open'|'done'|'archived'`, `channel: str`, `sort_order: int`, `created_at: int`, `updated_at: int`, `body?: str`

#### POST `/api/jobs`
- **Purpose:** Create a new job
- **Auth:** Session token
- **Request:** `{ title: str, body?: str, channel?: str }`
- **Response (200):** `Job` (single object)

#### PATCH `/api/jobs/{job_id}`
- **Purpose:** Update job (title, body, status)
- **Auth:** Session token
- **Request:** `{ title?: str, body?: str, status?: str }`
- **Response (200):** `Job`

#### DELETE `/api/jobs/{job_id}`
- **Purpose:** Delete a job
- **Auth:** Session token
- **Response (200):** `{ ok: bool }`

#### POST `/api/jobs/reorder`
- **Purpose:** Reorder jobs within a status group (kanban drag-drop)
- **Auth:** Session token
- **Request:** `{ status: str, ordered_ids: int[] }`  (top-to-bottom order)
- **Response (200):** `{ ok: bool, updated: Job[] }`

#### GET `/api/jobs/{job_id}/messages`
- **Purpose:** Get messages/thread for a job
- **Auth:** Session token
- **Response (200):** `JobMessage[]`
  - Fields: `id: int`, `index: int`, `job_id: int`, `sender: str`, `text: str`, `timestamp: int`, `resolved: bool`

#### POST `/api/jobs/{job_id}/messages`
- **Purpose:** Add message to job thread
- **Auth:** Session token
- **Request:** `{ text: str, sender?: str }`
- **Response (200):** `JobMessage`

#### DELETE `/api/jobs/{job_id}/messages/{msg_id}`
- **Purpose:** Delete message from job thread
- **Auth:** Session token
- **Response (200):** `{ ok: bool }`

#### POST `/api/jobs/{job_id}/messages/{msg_index}/resolve`
- **Purpose:** Mark job message as resolved
- **Auth:** Session token
- **Response (200):** `{ ok: bool }`

---

### Rules

#### GET `/api/rules`
- **Purpose:** Get all rules (active, pending, archived)
- **Auth:** Session token
- **Response (200):** `Rule[]`
  - Fields: `id: int`, `name: str`, `code: str`, `status: str`, `created_by: str`, `created_at: int`

#### GET `/api/rules/active`
- **Purpose:** Get only active rules
- **Auth:** Session token
- **Response (200):** `Rule[]`

#### GET `/api/rules/freshness`
- **Purpose:** Get timestamp of when rules were last synced/updated
- **Auth:** Session token
- **Response (200):** `{ freshness: int }`

#### POST `/api/rules/agent_sync/{agent_name}`
- **Purpose:** Trigger rule sync/refresh for a specific agent
- **Auth:** Session token
- **Response (200):** `{ ok: bool }`

#### POST `/api/rules/remind`
- **Purpose:** Send reminder about pending rules
- **Auth:** Session token
- **Response (200):** `{ count: int }`

---

### Sessions & Orchestration

#### GET `/api/sessions/active`
- **Purpose:** Get current active session (if any)
- **Auth:** Session token
- **Response (200):** `Session | null`
  - Fields: `id: str`, `template_id: str`, `status: str`, `agents: str[]`, `current_phase: int`, `current_agent: str`, `created_at: int`, `ended_at?: int`

#### GET `/api/sessions/active-all`
- **Purpose:** Get all active sessions across team
- **Auth:** Session token
- **Response (200):** `Session[]`

#### GET `/api/sessions/templates`
- **Purpose:** Get available session templates
- **Auth:** Session token
- **Response (200):** `SessionTemplate[]`

#### POST `/api/sessions/start`
- **Purpose:** Start a new session
- **Auth:** Session token
- **Request:** `{ template_id: str, agents: str[] }`
- **Response (200):** `Session`

#### POST `/api/sessions/{session_id}/end`
- **Purpose:** End a running session
- **Auth:** Session token
- **Response (200):** `{ ok: bool }`

#### POST `/api/sessions/request-draft`
- **Purpose:** Request draft (template planning)
- **Auth:** Session token
- **Request:** `{ template_id: str }`
- **Response (200):** `{ draft: SessionDraft }`

#### POST `/api/sessions/save-draft`
- **Purpose:** Save session draft
- **Auth:** Session token
- **Request:** `{ draft: SessionDraft }`
- **Response (200):** `Session`

#### DELETE `/api/sessions/templates/{template_id}`
- **Purpose:** Delete a session template
- **Auth:** Session token
- **Response (200):** `{ ok: bool }`

---

### Schedules (Cron Jobs)

#### GET `/api/schedules`
- **Purpose:** Get all scheduled messages
- **Auth:** Session token
- **Response (200):** `Schedule[]`
  - Fields: `id: str`, `text: str`, `cron: str`, `channel: str`, `enabled: bool`, `created_at: int`, `created_by: str`

#### POST `/api/schedules`
- **Purpose:** Create a scheduled message
- **Auth:** Session token
- **Request:** `{ text: str, cron: str, channel?: str }`
- **Response (200):** `Schedule`

#### DELETE `/api/schedules/{schedule_id}`
- **Purpose:** Delete a scheduled message
- **Auth:** Session token
- **Response (200):** `{ ok: bool }`

#### PATCH `/api/schedules/{schedule_id}/toggle`
- **Purpose:** Enable/disable a schedule
- **Auth:** Session token
- **Request:** `{ enabled: bool }`
- **Response (200):** `{ ok: bool }`

---

### Settings & Config

#### GET `/api/settings`
- **Purpose:** Get room settings
- **Auth:** Session token
- **Response (200):** `RoomSettings` (object with keys like `title`, `username`, `font`, `channels`, `history_limit`, `contrast`, etc.)

#### POST `/api/open-path`
- **Purpose:** Open a file/folder path in system (OS integration)
- **Auth:** Session token
- **Request:** `{ path: str }`
- **Response (200):** `{ ok: bool }`

#### DELETE `/api/hat/{agent_name}`
- **Purpose:** Remove hat (avatar decoration) for an agent
- **Auth:** Session token
- **Response (200):** `{ ok: bool }`

---

### Agent & Role Management

#### GET `/api/status`
- **Purpose:** Get full room status (agents, roles, etc.)
- **Auth:** Session token
- **Response (200):** Full status snapshot (agents list, settings, hats, etc.)

#### GET `/api/roles`
- **Purpose:** Get all agent roles
- **Auth:** Session token
- **Response (200):** `{ [agent_name: str]: role: str }`

#### POST `/api/roles/{agent_name}`
- **Purpose:** Set role/label for an agent
- **Auth:** Session token
- **Request:** `{ role: str }`
- **Response (200):** `{ ok: bool }`

#### POST `/api/register`
- **Purpose:** Register a new agent/user
- **Auth:** Optional
- **Request:** `{ name: str, color?: str, label?: str }`
- **Response (200):** `{ ok: bool }`

#### POST `/api/deregister/{name}`
- **Purpose:** Unregister an agent
- **Auth:** Optional
- **Response (200):** `{ ok: bool }`

#### POST `/api/heartbeat/{agent_name}`
- **Purpose:** Send keepalive heartbeat (agent still alive)
- **Auth:** Optional
- **Request:** None
- **Response (200):** `{ ok: bool }`

#### POST `/api/label/{name}`
- **Purpose:** Set custom label for an agent
- **Auth:** Optional
- **Request:** `{ label: str }`
- **Response (200):** `{ ok: bool }`

---

### Utility

#### GET `/api/platform`
- **Purpose:** Get platform/environment info
- **Auth:** Session token
- **Response (200):** `{ platform: str, python_version: str, ... }`

#### GET `/api/version_check`
- **Purpose:** Check for app updates
- **Auth:** Session token
- **Response (200):** `{ current: str, latest: str, update_available: bool }`

#### POST `/api/trigger-agent`
- **Purpose:** Manually trigger an agent to run
- **Auth:** Session token
- **Request:** `{ agent_name: str, prompt: str }`
- **Response (200):** `{ ok: bool, job_id?: int }`

---

## WebSocket Events (Dynamic Subscription Model)

**Event Structure:** `{ type: str, action?: str, data: any }`

### Snapshot Events (Sent on Connect)

Sent immediately after WS connection handshake:

| Type | Data | Purpose |
|------|------|---------|
| `settings` | `RoomSettings` | Room configuration (font, contrast, etc.) |
| `agents` | `Record<agent_name, AgentConfig>` | Registered agents with config |
| `base_colors` | `Record<agent_name, {color, label}>` | Base agent colors (for messages) |
| `todos` | `Record<msg_id, 'open'\|'done'>` | Todo/pinned message states |
| `rules` | `Rule[]` | All rules (active, pending, archived) |
| `hats` | `Record<agent_name, svg_string>` | Agent hats (avatar decorations) |
| `jobs` | `Job[]` | All jobs (kanban board) |
| `schedules` | `Schedule[]` | All schedules |
| `pending_instance` | `{ name: str }` | Late-connecting agent instance info |
| `status` | `{ version: str, agents_online: int, ... }` | Room status |

### Dynamic Broadcast Events (During Session)

**Message events:**
```
{ type: "message", action: "new", data: Message }
{ type: "message", action: "delete", data: { msg_id: int } }
{ type: "message", action: "pin", data: { msg_id: int, pinned: bool } }
{ type: "message", action: "typing", data: { agent: str, typing: bool } }
```

**Job events:**
```
{ type: "job", action: "created", data: Job }
{ type: "job", action: "updated", data: Job }
{ type: "job", action: "deleted", data: { id: int } }
{ type: "job", action: "reordered", data: { status: str, ids: int[] } }
```

**Rule events:**
```
{ type: "rule", action: "created", data: Rule }
{ type: "rule", action: "updated", data: Rule }
{ type: "rule", action: "activated", data: { id: int } }
{ type: "rule", action: "deleted", data: { id: int } }
```

**Schedule events:**
```
{ type: "schedule", action: "created", data: Schedule }
{ type: "schedule", action: "deleted", data: { id: str } }
{ type: "schedule", action: "toggled", data: { id: str, enabled: bool } }
```

**Session events:**
```
{ type: "session", action: "started", data: Session }
{ type: "session", action: "phase", data: { current_phase: int, current_agent: str } }
{ type: "session", action: "ended", data: { id: str } }
```

**Other events:**
```
{ type: "agent", action: "online", data: { name: str } }
{ type: "agent", action: "offline", data: { name: str } }
{ type: "agent", action: "renamed", data: { old: str, new: str } }
{ type: "agent", action: "hat", data: { name: str, svg: str } }
{ type: "instance", action: "pending", data: { name: str } }
{ type: "instance", action: "claimed", data: { name: str, claimed_by: str } }
```

---

## Data Types

### Message
```typescript
interface Message {
  id: number;
  sender: string;
  text: string;
  timestamp: number;
  channel: string;
  reply_to?: number;
  pinned?: boolean;
  todo_state?: 'open' | 'done';
  marked_for_rule?: boolean;
}
```

### Job
```typescript
interface Job {
  id: number;
  title: string;
  created_by: string;
  status: 'open' | 'done' | 'archived';  // NOT 'todo' | 'active' | 'done'
  channel: string;
  sort_order: number;
  created_at: number;
  updated_at: number;
  body?: string;
}
```

### Rule
```typescript
interface Rule {
  id: number;
  name: string;
  code: string;
  status: string;  // 'active', 'pending', 'archived'
  created_by: string;
  created_at: number;
}
```

### Schedule
```typescript
interface Schedule {
  id: string;  // UUID
  text: string;
  cron: string;
  channel: string;
  enabled: boolean;
  created_at: number;
  created_by: string;
}
```

---

## Authentication & Error Handling

### Authentication
- **Header:** `X-Session-Token: <token>`
- **Token Injection:** Backend injects token into HTML at `GET /`
- **WebSocket:** Token passed via header on WS upgrade

### Error Responses
- **401 Unauthorized:** Missing/invalid session token
- **400 Bad Request:** Invalid request shape/data
- **500 Server Error:** Unexpected error (check logs)

### Response Format
- Most responses return simplified objects (not wrapped)
- Success indicator: `ok: bool` field or HTTP status code
- Errors typically return HTTP error status + message

---

**Status:** ✅ Corrected (v2 — aligns with actual backend code)
**Next:** Update `web/src/types/index.ts` to match this contract
