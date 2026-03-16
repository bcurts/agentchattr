/**
 * Settings store — room settings from backend WS snapshot.
 * Populated by the `settings` event on connect.
 */
import { create } from 'zustand';
import type { RoomSettings } from '../types';

const DEFAULT_SETTINGS: RoomSettings = {
  title: 'AgentChattr',
  username: 'user',
  font: 'sans',
  channels: ['general'],
  history_limit: 'all',
  contrast: 'normal',
};

interface SettingsState {
  settings: RoomSettings;
  setSettings: (s: RoomSettings) => void;
}

export const useSettingsStore = create<SettingsState>((set) => ({
  settings: DEFAULT_SETTINGS,
  setSettings: (settings) => set({ settings }),
}));
