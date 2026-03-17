/**
 * useWebSocket — connect to /ws, bootstrap state, dispatch events to stores.
 *
 * Auth: ?token= query param (from window.__SESSION_TOKEN__ injected by backend)
 * Contract: plans/ui-rebuild/api-contract-strict.md
 *
 * Fix history dedup (P0): on each connect, clear messages before ingesting
 * history replay frames, then switch to live-append mode after `status` event.
 *
 * Fix clear channel scope (P0): `clear` event is scoped to channel.
 *
 * Fix sender identity (P1): do NOT send sender from client — backend resolves
 * identity from session/registration. Only send text + channel.
 *
 * Fix channels from settings (P1): settings snapshot populates settingsStore.
 */
import { useEffect, useRef, useCallback, useState } from 'react';
import { useChatStore } from '../stores/chatStore';
import { useJobsStore } from '../stores/jobsStore';
import { useAgentStore } from '../stores/agentStore';
import { useSettingsStore } from '../stores/settingsStore';
import { useRulesStore } from '../stores/rulesStore';
import type { WSEvent, Message, Job, Rule, RuleAction } from '../types';

declare global {
  interface Window { __SESSION_TOKEN__?: string; }
}

function getWsUrl(): string {
  const token = window.__SESSION_TOKEN__ ?? '';
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`;
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Track whether we're still in the initial history-replay phase.
  // On each connect: reset messages, enter replay mode, exit on `status`.
  const inReplayRef = useRef(false);
  const [connected, setConnected] = useState(false);

  const { addMessage, deleteMessages, updateMessage } = useChatStore();
  const { addJob, updateJob, removeJob, addJobMessage, deleteJobMessage } = useJobsStore();
  const { setAgents, setBaseColors, setHats, setTyping, setPresence } = useAgentStore();
  const { setSettings } = useSettingsStore();
  const { setRules, applyRuleEvent } = useRulesStore();

  const chatStore = useChatStore;
  const jobsStore = useJobsStore;

  const dispatch = useCallback((event: WSEvent) => {
    const e = event as any;

    switch (e.type) {

      // ── Snapshot events (sent once on connect) ──────────────────────────
      case 'settings':
        // FIX #4: populate channel list (and username) from backend settings
        if (e.data) {
          setSettings(e.data);
          // Phase 4 Fix #2: guard stale currentChannel after channel delete.
          // Backend broadcasts `settings` after every channel mutation.
          // If currentChannel no longer exists in the new channel list, fall
          // back to 'general' (always present per backend invariant).
          const newChannels: string[] = e.data.channels ?? ['general'];
          const current = chatStore.getState().currentChannel;
          if (!newChannels.includes(current)) {
            chatStore.getState().setCurrentChannel('general');
          }
        }
        break;
      case 'agents':
        setAgents(e.data ?? {});
        break;
      case 'base_colors':
        // Phase 2: wire base_colors to agentStore for fallback coloring
        if (e.data) setBaseColors(e.data);
        break;
      case 'todos':
        break; // TODO: wire to chat store
      case 'rules':
        // Phase 5: wire rules snapshot to rulesStore
        if (Array.isArray(e.data)) setRules(e.data as Rule[]);
        break;
      case 'hats':
        setHats(e.data ?? {});
        break;
      case 'jobs':
        jobsStore.getState().setJobs(e.data ?? []);
        break;
      case 'schedules':
        break; // TODO: schedulesStore
      case 'pending_instance':
        break; // TODO: naming lightbox
      case 'status':
        // Phase 2: wire presence data (available, busy, label, color, role)
        // `status` also signals end of replay phase.
        // IMPORTANT: backend status payload has a top-level scalar `paused: bool`
        // alongside the per-agent entries (app.py:866-869). Strip non-agent keys
        // before calling setPresence — only keep entries shaped like AgentPresence.
        inReplayRef.current = false;
        if (e.data && typeof e.data === 'object') {
          const agentPresence: Record<string, any> = {};
          for (const [key, val] of Object.entries(e.data as Record<string, unknown>)) {
            if (val !== null && typeof val === 'object' && 'available' in (val as object)) {
              agentPresence[key] = val;
            }
            // scalar entries like `paused` are intentionally dropped here
          }
          setPresence(agentPresence);
        }
        break;

      // ── Dynamic: messages ───────────────────────────────────────────────
      case 'message':
        // During replay phase we're safe to append (store was cleared on connect).
        // After replay, addMessage handles live messages.
        if (e.data) addMessage(e.data as Message);
        break;

      case 'delete':
        // FIX (was already correct — ids array)
        if (Array.isArray(e.ids)) deleteMessages(e.ids as number[]);
        break;

      case 'edit':
        if (e.message?.id != null) {
          updateMessage(e.message.id as number, e.message as Partial<Message>);
        }
        break;

      case 'typing':
        setTyping(
          e.agent as string,
          e.active as boolean,
          (e.status as 'checking' | 'working' | 'typing' | undefined) ?? 'typing',
          e.channel as string | undefined,
        );
        break;

      case 'clear':
        // FIX #2: clear only the specified channel, not all messages.
        // Backend sends {type:"clear", channel?: string}
        if (e.channel) {
          chatStore.getState().clearChannel(e.channel as string);
        } else {
          // no channel = clear all (e.g. /clear with no arg)
          chatStore.getState().setMessages([]);
        }
        break;

      case 'todo_update':
        break; // TODO

      // ── Dynamic: jobs ────────────────────────────────────────────────────
      case 'job': {
        const action = e.action as string;
        const data = e.data as any;
        if (action === 'create') addJob(data as Job);
        else if (action === 'update') updateJob(data.id, data);
        else if (action === 'delete') removeJob(data.id ?? data);
        else if (action === 'message') {
          // data: {job_id: number, message: JobMessage}
          if (data?.job_id != null && data?.message) {
            addJobMessage(data.job_id, data.message);
          }
        }
        else if (action === 'message_delete') {
          // data: {job_id: number, message_id: number}
          if (data?.job_id != null && data?.message_id != null) {
            deleteJobMessage(data.job_id, data.message_id);
          }
        }
        break;
      }

      // ── Dynamic: rules / schedules / sessions ────────────────────────────
      case 'rule':
        // Phase 5: apply live rule mutations
        if (e.action && e.data) applyRuleEvent(e.action as RuleAction, e.data as Rule);
        break;
      case 'schedule':  break; // TODO: schedulesStore
      case 'session':   break; // TODO: sessionsStore

      // ── Misc ─────────────────────────────────────────────────────────────
      case 'agent_renamed':   break; // TODO
      case 'channel_renamed':
        // Phase 4: rename channel refs in messages + unreadCounts + currentChannel
        if (e.old_name && e.new_name) {
          chatStore.getState().renameChannel(e.old_name as string, e.new_name as string);
        }
        break;
      case 'rules_remind':    break;
    }
  }, [addMessage, deleteMessages, updateMessage, setAgents, setBaseColors, setHats, setTyping, setPresence,
      addJob, updateJob, removeJob, addJobMessage, deleteJobMessage, setSettings,
      setRules, applyRuleEvent]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(getWsUrl());
    wsRef.current = ws;

    ws.onopen = () => {
      // FIX #1: clear message store before replaying history from backend.
      // This prevents duplicate messages on reconnect.
      chatStore.getState().setMessages([]);
      inReplayRef.current = true;
      setConnected(true);
    };

    ws.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data) as WSEvent;
        dispatch(event);
      } catch {
        // ignore malformed frames
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      inReplayRef.current = false;
      setConnected(false);
      reconnectTimer.current = setTimeout(connect, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [dispatch]);

  useEffect(() => {
    connect();
    return () => {
      reconnectTimer.current && clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  // FIX #3: do NOT include sender — backend resolves identity from session.
  // Callers pass only: { text, channel }
  const send = useCallback((payload: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload));
    }
  }, []);

  return { send, connected };
}
