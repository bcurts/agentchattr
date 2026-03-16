/**
 * Mehub UI — TypeScript types
 *
 * STRICT: derived 1:1 from backend source code.
 * Reference: plans/ui-rebuild/api-contract-strict.md
 * Sources: store.py, jobs.py, rules.py, schedules.py, session_store.py, app.py
 */

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

export interface Attachment {
  name: string;
  url: string;
}

// ---------------------------------------------------------------------------
// Message (store.py:56–69)
// ---------------------------------------------------------------------------

export interface Message {
  id: number;
  sender: string;
  text: string;
  type: string;             // "chat" | "system" | "job_proposal" | ...
  timestamp: number;        // float epoch
  time: string;             // "HH:MM:SS"
  attachments: Attachment[];
  channel: string;
  reply_to?: number;
  metadata?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Job (jobs.py:114–128)
// ---------------------------------------------------------------------------

export type JobStatus = 'open' | 'done' | 'archived';

export interface Job {
  id: number;
  type: string;             // "job"
  title: string;
  body: string;
  status: JobStatus;
  channel: string;
  created_by: string;
  assignee: string;         // "" when unassigned
  anchor_msg_id: number | null;
  messages: JobMessage[];
  created_at: number;
  updated_at: number;
  sort_order: number;
}

// jobs.py:195–205
export interface JobMessage {
  id: number;               // index in messages array
  sender: string;
  text: string;
  time: string;             // "HH:MM:SS"
  timestamp: number;
  attachments: Attachment[];
  type?: string;            // only present for non-chat messages
}

// ---------------------------------------------------------------------------
// Rule (rules.py:121–128)
// ---------------------------------------------------------------------------

export type RuleStatus = 'pending' | 'active' | 'draft' | 'archived';

export interface Rule {
  id: number;
  text: string;             // the rule content (NOT "name" or "code")
  author: string;
  reason: string;
  status: RuleStatus;
  created_at: number;
}

// ---------------------------------------------------------------------------
// Schedule (schedules.py:167–180)
// ---------------------------------------------------------------------------

export interface Schedule {
  id: string;               // UUID[:8]
  prompt: string;           // the prompt text (NOT "text" or "cron")
  targets: string[];        // agent names without "@"
  channel: string;
  interval_seconds: number; // recurrence interval (NOT "cron")
  daily_at: string | null;  // "HH:MM" for daily schedule
  next_run: number;
  created_at: number;
  last_run: number | null;
  active: boolean;          // NOT "enabled"
  one_shot: boolean;
  created_by: string;
}

// ---------------------------------------------------------------------------
// Session (session_store.py)
// ---------------------------------------------------------------------------

export type SessionStatus = 'pending' | 'active' | 'complete' | 'interrupted';

export interface Session {
  id: string;
  status: SessionStatus;
  // Additional fields depend on session_store.py — extend as discovered
  [key: string]: unknown;
}

export interface SessionTemplate {
  id: string;
  name: string;
  description?: string;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Agents / Registry
// ---------------------------------------------------------------------------

export interface AgentConfig {
  color: string;
  label: string;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Room Settings
// ---------------------------------------------------------------------------

export interface RoomSettings {
  title: string;
  username: string;
  font: string;
  channels: string[];
  history_limit: 'all' | number;
  contrast: string;
  [key: string]: unknown;   // room_settings is open-ended
}

// ---------------------------------------------------------------------------
// WebSocket Events
//
// All events: { type: string, action?: string, data?: any, ...extras }
//
// Snapshot types (sent on connect, no action field):
//   settings, agents, base_colors, todos, rules, hats, jobs, schedules,
//   pending_instance, message (history), status
//
// Dynamic types with action field: rule, job, schedule, session
// Dynamic types without action field:
//   message, delete, typing, clear, todo_update, settings, hats,
//   agents, status, rules_remind, agent_renamed, channel_renamed
// ---------------------------------------------------------------------------

// Action unions per type
export type RuleAction    = 'propose' | 'activate' | 'deactivate' | 'edit' | 'delete';
export type JobAction     = 'create'  | 'update'   | 'message'    | 'message_delete' | 'delete';
export type ScheduleAction= 'create'  | 'update'   | 'delete';
export type SessionAction = 'create'  | 'update'   | 'complete'   | 'interrupt';

export interface WSEventBase {
  type: string;
  action?: string;
  data?: unknown;
}

// Specific typed events
export interface WSMessageEvent {
  type: 'message';
  data: Message;
}

export interface WSDeleteEvent {
  type: 'delete';
  ids: number[];
}

export interface WSTypingEvent {
  type: 'typing';
  agent: string;
  active: boolean;
}

export interface WSTodoUpdateEvent {
  type: 'todo_update';
  data: { id: number; status: string | null };
}

export interface WSRuleEvent {
  type: 'rule';
  action: RuleAction;
  data: Rule;
}

export interface WSJobEvent {
  type: 'job';
  action: JobAction;
  data: Job | { job_id: number; message: JobMessage } | { job_id: number; message_id: number };
}

export interface WSScheduleEvent {
  type: 'schedule';
  action: ScheduleAction;
  data: Schedule;
}

export interface WSSessionEvent {
  type: 'session';
  action: SessionAction;
  data: Session;
}

export interface WSAgentRenamedEvent {
  type: 'agent_renamed';
  old_name: string;
  new_name: string;
}

export interface WSChannelRenamedEvent {
  type: 'channel_renamed';
  old_name: string;
  new_name: string;
}

export type WSEvent =
  | WSMessageEvent
  | WSDeleteEvent
  | WSTypingEvent
  | WSTodoUpdateEvent
  | WSRuleEvent
  | WSJobEvent
  | WSScheduleEvent
  | WSSessionEvent
  | WSAgentRenamedEvent
  | WSChannelRenamedEvent
  | WSEventBase; // catch-all for settings, hats, agents, status, etc.

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

export type Nullable<T> = T | null;
export type Optional<T> = T | undefined;
