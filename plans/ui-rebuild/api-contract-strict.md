# API Contract — Strict (Code-Based)

**Generated:** 2026-03-14 (Sonnet strict pass)
**Source:** `app.py`, `jobs.py`, `rules.py`, `schedules.py`, `store.py`, `session_store.py`
**Method:** Line-by-line extraction, no inference

---

## Table 1: Endpoint Inventory

### 45 `/api/*` endpoints + 1 `/uploads/*` = 46 total

| # | Method | Path | Line in app.py |
|---|--------|------|---------------|
| 1 | POST | `/api/upload` | 1408 |
| 2 | GET | `/api/messages` | 1432 |
| 3 | POST | `/api/send` | 1440 |
| 4 | GET | `/api/status` | 1466 |
| 5 | GET | `/api/settings` | 1473 |
| 6 | DELETE | `/api/hat/{agent_name}` | 1478 |
| 7 | GET | `/api/schedules` | 1487 |
| 8 | POST | `/api/schedules` | 1492 |
| 9 | DELETE | `/api/schedules/{schedule_id}` | 1525 |
| 10 | PATCH | `/api/schedules/{schedule_id}/toggle` | 1533 |
| 11 | GET | `/api/jobs` | 1541 |
| 12 | POST | `/api/messages/{msg_id}/demote` | 1549 |
| 13 | POST | `/api/messages/{msg_id}/resolve_rule_proposal` | 1616 |
| 14 | POST | `/api/messages/{msg_id}/demote_rule_proposal` | 1655 |
| 15 | POST | `/api/trigger-agent` | 1685 |
| 16 | POST | `/api/jobs` | 1721 |
| 17 | PATCH | `/api/jobs/{job_id}` | 1761 |
| 18 | POST | `/api/jobs/reorder` | 1777 |
| 19 | GET | `/api/jobs/{job_id}/messages` | 1789 |
| 20 | POST | `/api/jobs/{job_id}/messages` | 1798 |
| 21 | DELETE | `/api/jobs/{job_id}/messages/{msg_id}` | 1840 |
| 22 | POST | `/api/jobs/{job_id}/messages/{msg_index}/resolve` | 1849 |
| 23 | DELETE | `/api/jobs/{job_id}` | 1876 |
| 24 | GET | `/api/roles` | 1889 |
| 25 | POST | `/api/roles/{agent_name}` | 1896 |
| 26 | GET | `/api/rules` | 1912 |
| 27 | GET | `/api/rules/active` | 1918 |
| 28 | POST | `/api/rules/remind` | 1926 |
| 29 | POST | `/api/rules/agent_sync/{agent_name}` | 1939 |
| 30 | GET | `/api/rules/freshness` | 1953 |
| 31 | POST | `/api/register` | 1959 |
| 32 | POST | `/api/deregister/{name}` | 2002 |
| 33 | POST | `/api/label/{name}` | 2036 |
| 34 | POST | `/api/heartbeat/{agent_name}` | 2072 |
| 35 | GET | `/api/platform` | 2132 |
| 36 | POST | `/api/open-path` | 2139 |
| 37 | GET | `/api/sessions/templates` | 2188 |
| 38 | GET | `/api/sessions/active` | 2195 |
| 39 | GET | `/api/sessions/active-all` | 2203 |
| 40 | POST | `/api/sessions/start` | 2210 |
| 41 | POST | `/api/sessions/{session_id}/end` | 2274 |
| 42 | POST | `/api/sessions/request-draft` | 2286 |
| 43 | POST | `/api/sessions/save-draft` | 2322 |
| 44 | DELETE | `/api/sessions/templates/{template_id}` | 2345 |
| 45 | GET | `/api/version_check` | 2448 |
| — | GET | `/uploads/{filename}` | 2482 |

**Key auth notes:**
- `/api/send` uses `Authorization: Bearer <token>` (agent registration token), NOT `X-Session-Token`
- All other `/api/*` endpoints use `X-Session-Token` header
- WebSocket `/ws` authenticates via query param: `?token=<session_token>` (app.py:1014)

**Key response shapes (exact, from code):**
- `POST /api/upload` → `{"name": <original_filename>, "url": "/uploads/<uuid_ext>"}` (app.py:1426–1429)
- `POST /api/jobs/reorder` → `{"ok": True, "updated": <count: int>}` (app.py:1786)
- `PATCH /api/schedules/{id}/toggle` → full Schedule object (app.py:1538 returns `result`)
- `POST /api/trigger-agent` request body: `{"agent": str, "message": str, "channel"?: str}` (app.py:1689–1691)
- `GET /api/rules/active` → `{"epoch": int, "rules": [str, ...], "refresh_interval": int}` (rules.py:105–108 + app.py:1922)

---

## Table 2: WebSocket Event Inventory

### Auth
```
ws://host/ws?token=<session_token>
```

### A. Snapshot Events (sent on connect, in order)

| # | Type | Payload shape | Source line |
|---|------|--------------|-------------|
| 1 | `settings` | `RoomSettings` dict | app.py:1026 |
| 2 | `agents` | `Record<name, AgentConfig>` | app.py:1030 |
| 3 | `base_colors` | `Record<name, {color, label}>` | app.py:1036 |
| 4 | `todos` | `Record<msg_id_str, status_str>` | app.py:1039 |
| 5 | `rules` | `Rule[]` (full list) | app.py:1042 |
| 6 | `hats` | `Record<name, svg_str>` | app.py:1045 |
| 7 | `jobs` | `Job[]` (full list) | app.py:1048 |
| 8 | `schedules` | `Schedule[]` (full list) | app.py:1051 |
| 9 | `pending_instance` | `{name, base, label, color}` | app.py:1057 |
| 10 | `message` | `Message` dict (history replay) | app.py:1077 |
| 11 | `status` | status dict | app.py (broadcast_status) |

### B. Dynamic Broadcast Events (runtime)

**Structure: `{"type": <type>, "action"?: <action>, "data": <payload>}`**

Note: some events have NO `action` field.

| WS Type | action | data payload | Trigger |
|---------|--------|-------------|---------|
| `message` | *(none)* | `Message` dict | New chat message (store callback) |
| `delete` | *(none)* | `{"ids": [int, ...]}` | Messages deleted (WS handler, app.py:1128) |
| `typing` | *(none)* | `{"agent": str, "active": bool}` | Typing indicator (broadcast_typing) |
| `clear` | *(none)* | `{"channel"?: str}` | Chat cleared (broadcast_clear) |
| `todo_update` | *(none)* | `{"id": int, "status": str\|null}` | Todo toggled (broadcast_todo_update) |
| `settings` | *(none)* | `RoomSettings` dict | Settings changed (broadcast_settings) |
| `hats` | *(none)* | `Record<name, svg_str>` | Hat updated (broadcast_hats) |
| `agents` | *(none)* | `Record<name, AgentConfig>` | Agent registered/deregistered (broadcast_agents) |
| `status` | *(none)* | status dict | Periodic/on change (broadcast_status) |
| `rules_remind` | *(none)* | `{}` | Remind triggered (app.py:1930) |
| `agent_renamed` | *(none)* | `{"type":"agent_renamed","old_name":str,"new_name":str}` | Agent rename (app.py:1294) |
| `channel_renamed` | *(none)* | `{"type":"channel_renamed","old_name":str,"new_name":str}` | Channel rename (app.py:1371) |
| `rule` | `"propose"` | `Rule` dict | Rule proposed (rules.py:132) |
| `rule` | `"activate"` | `Rule` dict | Rule activated (rules.py:149) |
| `rule` | `"deactivate"` | `Rule` dict | Rule deactivated/archived (rules.py:183) |
| `rule` | `"edit"` | `Rule` dict | Rule edited or made draft (rules.py:166,203) |
| `rule` | `"delete"` | `Rule` dict | Rule deleted (rules.py:219) |
| `job` | `"create"` | `Job` dict | Job created (jobs.py:132) |
| `job` | `"update"` | `Job` dict | Job status/title/assignee/order changed (jobs.py:157,171,185,327) |
| `job` | `"message"` | `{"job_id":int,"message":JobMessage}` | Job thread message added (jobs.py:214) |
| `job` | `"message_delete"` | `{"job_id":int,"message_id":int}` | Job thread message deleted (jobs.py:256) |
| `job` | `"delete"` | `Job` dict | Job deleted (jobs.py:270) |
| `schedule` | `"create"` | `Schedule` dict | Schedule created (schedules.py:183) |
| `schedule` | `"update"` | `Schedule` dict | Schedule executed or toggled (schedules.py:211,237) |
| `schedule` | `"delete"` | `Schedule` dict | Schedule deleted (schedules.py:224) |
| `session` | `"create"` | `Session` dict | Session created (session_store.py:174) |
| `session` | `"update"` | `Session` dict | Session state changed (session_store.py:212,229,243,256,269) |
| `session` | `"complete"` | `Session` dict | Session completed (session_store.py:284) |
| `session` | `"interrupt"` | `Session` dict | Session interrupted (session_store.py:298) |

---

## Table 3: Data Schema Mapping

### Message (store.py:56–69)
```typescript
interface Message {
  id: number;                        // int, monotonic
  sender: string;
  text: string;
  type: string;                      // "chat" | "system" | "job_proposal" | ...
  timestamp: number;                 // float epoch
  time: string;                      // "HH:MM:SS"
  attachments: Attachment[];         // {name: string, url: string}[]
  channel: string;                   // default "general"
  reply_to?: number;                 // message id
  metadata?: Record<string, any>;
}
```

### Job (jobs.py:114–128)
```typescript
interface Job {
  id: number;                        // int, monotonic
  type: string;                      // "job" (literal)
  title: string;                     // max 120 chars
  body: string;                      // max 1000 chars
  status: 'open' | 'done' | 'archived';
  channel: string;
  created_by: string;
  assignee: string;                  // "" if unassigned
  anchor_msg_id: number | null;
  messages: JobMessage[];
  created_at: number;                // float epoch
  updated_at: number;                // float epoch
  sort_order: number;                // int
}

interface JobMessage {
  id: number;                        // index in messages array
  sender: string;
  text: string;
  time: string;                      // "HH:MM:SS"
  timestamp: number;                 // float epoch
  attachments: Attachment[];
  type?: string;                     // only present for non-chat
}
```

### Rule (rules.py:121–128)
```typescript
interface Rule {
  id: number;                        // int, monotonic
  text: string;                      // max 160 chars (MAX_TEXT_CHARS)
  author: string;
  reason: string;                    // max 240 chars (MAX_REASON_CHARS)
  status: 'pending' | 'active' | 'draft' | 'archived';
  created_at: number;                // float epoch
}
```

### Schedule (schedules.py:167–180)
```typescript
interface Schedule {
  id: string;                        // UUID[:8]
  prompt: string;                    // max 500 chars (NOT "text")
  targets: string[];                 // agent names without "@"
  channel: string;                   // default "general"
  interval_seconds: number;          // recurrence (NOT "cron")
  daily_at: string | null;           // "HH:MM" for daily schedule
  next_run: number;                  // float epoch
  created_at: number;                // float epoch
  last_run: number | null;
  active: boolean;                   // NOT "enabled"
  one_shot: boolean;                 // auto-delete after firing
  created_by: string;
}
```

### Session (session_store.py)
```typescript
// (inferred from session_store fields — exact structure requires session_store.py inspection)
interface Session {
  id: string;
  status: 'pending' | 'active' | 'complete' | 'interrupted';
  // ... other fields from session_store
}
```

---

## Breaking Diff: Initial Docs → Strict

| Field / Event | Initial (wrong) | Strict (correct) |
|---------------|----------------|-----------------|
| `Message.id` type | `string` | `number` |
| `Job.id` type | `string` | `number` |
| `Job.status` values | `'todo'\|'active'\|'done'` | `'open'\|'done'\|'archived'` |
| `Rule.name` field | existed | **does not exist** |
| `Rule.code` field | existed | **does not exist** |
| `Rule.text` field | missing | **exists** (the rule content) |
| `Rule.author` field | missing | **exists** |
| `Rule.reason` field | missing | **exists** |
| `Rule.status` values | `'draft'\|'active'\|'archived'` | `'pending'\|'active'\|'draft'\|'archived'` |
| `Schedule.text` field | existed | **does not exist** |
| `Schedule.cron` field | existed | **does not exist** |
| `Schedule.enabled` field | existed | **does not exist** |
| `Schedule.prompt` field | missing | **exists** |
| `Schedule.targets` field | missing | **exists** (`string[]`) |
| `Schedule.active` field | missing | **exists** (`boolean`) |
| `Schedule.interval_seconds` | missing | **exists** |
| `Schedule.daily_at` field | missing | **exists** |
| `Schedule.one_shot` field | missing | **exists** |
| WS event names | `job_created`, `job_updated`... | `{type:"job", action:"create"\|"update"...}` |
| WS `message` event | `{type:"message", action:"new"}` | `{type:"message"}` (no action field) |
| WS `delete` event | `{type:"message", action:"delete"}` | `{type:"delete", ids:[int,...]}` (separate type) |
| WS `typing` event | `{type:"message", action:"typing"}` | `{type:"typing", agent:str, active:bool}` |
| WS `rule` actions | `propose\|activate\|deactivate\|edit\|delete` | same ✓ |
| WS `job` actions | `create\|update\|message\|message_delete\|delete` | same ✓ |
| WS `schedule` actions | `create\|update\|delete` | same ✓ |
| WS `session` actions | `started\|phase_changed\|ended` | `create\|update\|complete\|interrupt` |
| WS auth | header | `?token=` query param |
| `/api/send` auth | `X-Session-Token` | `Authorization: Bearer` |
| `/api/upload` response | `{filename, url}` | `{name, url}` |
| `/api/jobs/reorder` response | `{ok, updated: Job[]}` | `{ok, updated: number}` (count) |
| `/api/schedules/{id}/toggle` response | `{ok: bool}` | full `Schedule` object |
| `/api/trigger-agent` request | `{agent_name, prompt}` | `{agent, message, channel?}` |
| `/api/rules/active` response | `Rule[]` | `{epoch: int, rules: string[], refresh_interval: int}` |
| `baseline.md` React version | React 18 | React 19 (actual in package.json) |
