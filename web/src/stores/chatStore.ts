/**
 * Chat store — messages, channels, reply state
 *
 * Phase 4 additions:
 * - unreadCounts: per-channel count of unread messages
 * - markRead(channel): reset unread count for a channel
 * - bumpUnread(channel): increment unread count
 * - renameChannel(old, new): update channel refs in messages
 */
import { create } from 'zustand';
import type { Message } from '../types';

const ACTIVE_CHANNEL_KEY = 'mehub_active_channel';

function loadPersistedChannel(): string {
  try {
    return localStorage.getItem(ACTIVE_CHANNEL_KEY) ?? 'general';
  } catch {
    return 'general';
  }
}

interface ChatState {
  messages: Message[];
  currentChannel: string;
  replyTarget: Message | null;
  unreadCounts: Record<string, number>;

  setMessages: (msgs: Message[]) => void;
  addMessage: (msg: Message) => void;
  deleteMessages: (ids: number[]) => void;
  updateMessage: (id: number, patch: Partial<Message>) => void;

  setCurrentChannel: (ch: string) => void;
  setReplyTarget: (msg: Message | null) => void;
  clearChannel: (ch: string) => void;
  markRead: (channel: string) => void;
  bumpUnread: (channel: string) => void;
  renameChannel: (oldName: string, newName: string) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  messages: [],
  currentChannel: loadPersistedChannel(),
  replyTarget: null,
  unreadCounts: {},

  setMessages: (msgs) => set({ messages: msgs }),

  addMessage: (msg) =>
    set((s) => {
      // deduplicate by message id — ignore if already present
      if (s.messages.some((m) => m.id === msg.id)) return s;
      const next = [...s.messages, msg];
      // bump unread if message is in a channel other than the active one
      if (msg.channel && msg.channel !== s.currentChannel) {
        return {
          messages: next,
          unreadCounts: {
            ...s.unreadCounts,
            [msg.channel]: (s.unreadCounts[msg.channel] ?? 0) + 1,
          },
        };
      }
      return { messages: next };
    }),

  deleteMessages: (ids) =>
    set((s) => ({ messages: s.messages.filter((m) => !ids.includes(m.id)) })),

  updateMessage: (id, patch) =>
    set((s) => ({
      messages: s.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    })),

  setCurrentChannel: (ch) => {
    try { localStorage.setItem(ACTIVE_CHANNEL_KEY, ch); } catch { /* noop */ }
    set((s) => ({
      currentChannel: ch,
      // clear unread for newly-active channel
      unreadCounts: { ...s.unreadCounts, [ch]: 0 },
    }));
  },

  setReplyTarget: (msg) => set({ replyTarget: msg }),

  clearChannel: (ch) =>
    set((s) => ({ messages: s.messages.filter((m) => m.channel !== ch) })),

  markRead: (channel) =>
    set((s) => ({ unreadCounts: { ...s.unreadCounts, [channel]: 0 } })),

  bumpUnread: (channel) =>
    set((s) => ({
      unreadCounts: {
        ...s.unreadCounts,
        [channel]: (s.unreadCounts[channel] ?? 0) + 1,
      },
    })),

  renameChannel: (oldName, newName) =>
    set((s) => {
      const messages = s.messages.map((m) =>
        m.channel === oldName ? { ...m, channel: newName } : m
      );
      const unreadCounts = { ...s.unreadCounts };
      if (oldName in unreadCounts) {
        unreadCounts[newName] = unreadCounts[oldName];
        delete unreadCounts[oldName];
      }
      const currentChannel = s.currentChannel === oldName ? newName : s.currentChannel;
      if (currentChannel !== s.currentChannel) {
        try { localStorage.setItem(ACTIVE_CHANNEL_KEY, currentChannel); } catch { /* noop */ }
      }
      return { messages, unreadCounts, currentChannel };
    }),
}));
