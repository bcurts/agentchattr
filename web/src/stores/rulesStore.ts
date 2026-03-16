/**
 * Rules store — populated by WS `rules` snapshot + dynamic `rule` events.
 * Phase 5: read-only display; full CRUD deferred to later phase.
 */
import { create } from 'zustand';
import type { Rule, RuleAction } from '../types';

interface RulesState {
  rules: Rule[];
  setRules: (rules: Rule[]) => void;
  applyRuleEvent: (action: RuleAction, data: Rule) => void;
}

export const useRulesStore = create<RulesState>((set) => ({
  rules: [],

  setRules: (rules) => set({ rules }),

  applyRuleEvent: (action, data) =>
    set((s) => {
      switch (action) {
        case 'propose':
          // Add new pending rule (avoid duplicate by id)
          if (s.rules.find(r => r.id === data.id)) return s;
          return { rules: [...s.rules, data] };
        case 'activate':
        case 'deactivate':
        case 'edit':
          return {
            rules: s.rules.map(r => r.id === data.id ? { ...r, ...data } : r),
          };
        case 'delete':
          return { rules: s.rules.filter(r => r.id !== data.id) };
        default:
          return s;
      }
    }),
}));
