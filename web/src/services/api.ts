/**
 * api.ts — thin REST client for the Mehub backend.
 * Auth: X-Session-Token header (injected from window.__SESSION_TOKEN__)
 * Exception: /api/send uses Authorization: Bearer
 *
 * All calls return parsed JSON or throw on non-2xx.
 */

declare global {
  interface Window { __SESSION_TOKEN__?: string; }
}

function sessionHeaders(): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Session-Token': window.__SESSION_TOKEN__ ?? '',
  };
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { ...sessionHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error((err as any).error ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

// ── Jobs ──────────────────────────────────────────────────────────────────

export interface CreateJobPayload {
  title: string;
  body?: string;
  channel?: string;
  created_by?: string;
  assignee?: string;
  anchor_msg_id?: number;
}

export function createJob(payload: CreateJobPayload) {
  return apiFetch('/api/jobs', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function demoteProposal(messageId: number) {
  return apiFetch(`/api/messages/${messageId}/demote`, {
    method: 'POST',
  });
}

export interface UpdateJobPayload {
  status?: string;
  title?: string;
  assignee?: string;
}

export function updateJobApi(jobId: number, payload: UpdateJobPayload) {
  return apiFetch(`/api/jobs/${jobId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export interface PostJobMessagePayload {
  text: string;
  sender?: string;
  attachments?: object[];
}

export function postJobMessage(jobId: number, payload: PostJobMessagePayload) {
  return apiFetch(`/api/jobs/${jobId}/messages`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function deleteJob(jobId: number, permanent = false) {
  const suffix = permanent ? '?permanent=true' : '';
  return apiFetch(`/api/jobs/${jobId}${suffix}`, { method: 'DELETE' });
}

// ── Channels ────────────────────────────────────────────────────────────────
// Channel management is via WebSocket events (not REST).
// Use the send() callback from useWebSocket to fire these payloads.

export interface ChannelCreatePayload {
  type: 'channel_create';
  name: string;
}

export interface ChannelDeletePayload {
  type: 'channel_delete';
  name: string;
}

export interface ChannelRenamePayload {
  type: 'channel_rename';
  old_name: string;
  new_name: string;
}

export function channelCreatePayload(name: string): ChannelCreatePayload {
  return { type: 'channel_create', name: name.trim().toLowerCase() };
}

export function channelDeletePayload(name: string): ChannelDeletePayload {
  return { type: 'channel_delete', name };
}

export function channelRenamePayload(oldName: string, newName: string): ChannelRenamePayload {
  return { type: 'channel_rename', old_name: oldName, new_name: newName.trim().toLowerCase() };
}
