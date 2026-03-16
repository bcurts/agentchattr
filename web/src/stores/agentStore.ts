/**
 * Agent store — presence, configs, hats, base colors, status
 *
 * Data sources (WS snapshot + dynamic events):
 *   agents      ← {type:"agents", data: Record<name, AgentConfig>}
 *   base_colors ← {type:"base_colors", data: Record<name, {color, label}>}
 *   hats        ← {type:"hats", data: Record<name, svg_str>}
 *   status      ← {type:"status", data: Record<name, {available, busy, label, color, role}>}
 *   typing      ← {type:"typing", agent, active}
 */
import { create } from 'zustand';
import type { AgentConfig } from '../types';

export interface AgentPresence {
  available: boolean;   // online / registered
  busy: boolean;        // currently working
  label: string;
  color: string;
  role?: string;
}

interface AgentState {
  // From `agents` snapshot: registry config (color, label, etc.)
  agents: Record<string, AgentConfig>;
  // From `base_colors` snapshot: static colors from config file
  baseColors: Record<string, { color: string; label: string }>;
  // From `hats` snapshot/dynamic: SVG hat strings per agent
  hats: Record<string, string>;
  // From `status` dynamic: live presence (available, busy)
  presence: Record<string, AgentPresence>;
  // From `typing` dynamic: set of agents currently typing
  typingAgents: Set<string>;

  setAgents: (agents: Record<string, AgentConfig>) => void;
  setBaseColors: (bc: Record<string, { color: string; label: string }>) => void;
  setHats: (hats: Record<string, string>) => void;
  setPresence: (presence: Record<string, AgentPresence>) => void;
  setTyping: (name: string, active: boolean) => void;

  // Derived helper: get display color for an agent (registry > base_colors > fallback)
  getColor: (name: string) => string;
  getLabel: (name: string) => string;
}

export const useAgentStore = create<AgentState>((set, get) => ({
  agents: {},
  baseColors: {},
  hats: {},
  presence: {},
  typingAgents: new Set(),

  setAgents: (agents) => set({ agents }),
  setBaseColors: (bc) => set({ baseColors: bc }),
  setHats: (hats) => set({ hats }),
  setPresence: (presence) => set({ presence }),

  setTyping: (name, active) =>
    set((s) => {
      const next = new Set(s.typingAgents);
      active ? next.add(name) : next.delete(name);
      return { typingAgents: next };
    }),

  getColor: (name) => {
    const s = get();
    return s.agents[name]?.color
      ?? s.baseColors[name]?.color
      ?? s.presence[name]?.color
      ?? hashColor(name);
  },

  getLabel: (name) => {
    const s = get();
    return s.agents[name]?.label
      ?? s.baseColors[name]?.label
      ?? s.presence[name]?.label
      ?? name;
  },
}));

/** Deterministic color from name for agents not in registry */
export function hashColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue}, 55%, 62%)`;
}
