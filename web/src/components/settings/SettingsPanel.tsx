/**
 * SettingsPanel — Phase 5
 *
 * Slide-out right panel for room settings.
 * Saves via WS `update_settings` event (app.py:1233).
 *
 * Editable fields (backend-supported):
 *   - font:          'sans' | 'mono' | 'serif'
 *   - contrast:      'normal' | 'high'
 *   - history_limit: 'all' | 50 | 100 | 500 | 1000
 *   - username:      string (trimmed, falls back to 'user')
 */
import { useState, useEffect } from 'react';
import { useSettingsStore } from '../../stores/settingsStore';

interface Props {
  onSend: (payload: object) => void;
  onClose: () => void;
}

const FONT_OPTIONS: { value: string; label: string }[] = [
  { value: 'sans',  label: 'Sans-serif' },
  { value: 'mono',  label: 'Monospace'  },
  { value: 'serif', label: 'Serif'      },
];

const HISTORY_OPTIONS: { value: string | number; label: string }[] = [
  { value: 'all', label: 'All messages' },
  { value: 50,    label: 'Last 50'      },
  { value: 100,   label: 'Last 100'     },
  { value: 500,   label: 'Last 500'     },
  { value: 1000,  label: 'Last 1000'    },
];

const ROW: React.CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  marginBottom: 16,
};

const LABEL: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 700,
  color: '#55556a',
  textTransform: 'uppercase',
  letterSpacing: 0.8,
};

const INPUT_BASE: React.CSSProperties = {
  background: '#0f0f17',
  border: '1px solid #2a2a4a',
  borderRadius: 5,
  color: '#e8e8f0',
  fontSize: 13,
  padding: '5px 8px',
  outline: 'none',
  width: '100%',
  boxSizing: 'border-box',
};

export function SettingsPanel({ onSend, onClose }: Props) {
  const settings = useSettingsStore(s => s.settings);

  // Local draft state — only pushed to backend on Save
  const [font, setFont]         = useState(settings.font ?? 'sans');
  const [contrast, setContrast] = useState(settings.contrast ?? 'normal');
  const [historyLimit, setHistoryLimit] = useState<string | number>(settings.history_limit ?? 'all');
  const [username, setUsername] = useState(settings.username ?? 'user');
  const [saved, setSaved]       = useState(false);

  // Sync local draft if store changes externally (e.g. another client)
  useEffect(() => {
    setFont(settings.font ?? 'sans');
    setContrast(settings.contrast ?? 'normal');
    setHistoryLimit(settings.history_limit ?? 'all');
    setUsername(settings.username ?? 'user');
  }, [settings]);

  function handleSave() {
    const patch: Record<string, unknown> = {
      font,
      contrast,
      history_limit: historyLimit,
      username: username.trim() || 'user',
    };
    onSend({ type: 'update_settings', data: patch });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  }

  return (
    <div style={{
      width: 280,
      flexShrink: 0,
      background: '#0d0d1a',
      borderLeft: '1px solid #2a2a4a',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        height: 44,
        borderBottom: '1px solid #2a2a4a',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 12px',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>Settings</span>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#55556a',
            cursor: 'pointer',
            fontSize: 18,
            lineHeight: 1,
            padding: '0 2px',
          }}
        >×</button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 12px' }}>

        {/* Username */}
        <div style={ROW}>
          <span style={LABEL}>Username</span>
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            maxLength={32}
            placeholder="user"
            style={INPUT_BASE}
          />
        </div>

        {/* Font */}
        <div style={ROW}>
          <span style={LABEL}>Font</span>
          <div style={{ display: 'flex', gap: 6 }}>
            {FONT_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setFont(opt.value)}
                style={{
                  flex: 1,
                  background: font === opt.value ? '#1f2b47' : 'transparent',
                  border: `1px solid ${font === opt.value ? '#7c6af7' : '#2a2a4a'}`,
                  borderRadius: 5,
                  color: font === opt.value ? '#7c6af7' : '#8888aa',
                  cursor: 'pointer',
                  fontSize: 11,
                  padding: '4px 0',
                  fontFamily: opt.value === 'mono' ? 'monospace' : opt.value === 'serif' ? 'serif' : 'sans-serif',
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Contrast */}
        <div style={ROW}>
          <span style={LABEL}>Contrast</span>
          <div style={{ display: 'flex', gap: 6 }}>
            {(['normal', 'high'] as const).map(val => (
              <button
                key={val}
                onClick={() => setContrast(val)}
                style={{
                  flex: 1,
                  background: contrast === val ? '#1f2b47' : 'transparent',
                  border: `1px solid ${contrast === val ? '#7c6af7' : '#2a2a4a'}`,
                  borderRadius: 5,
                  color: contrast === val ? '#7c6af7' : '#8888aa',
                  cursor: 'pointer',
                  fontSize: 11,
                  padding: '4px 0',
                  textTransform: 'capitalize',
                }}
              >
                {val}
              </button>
            ))}
          </div>
        </div>

        {/* History limit */}
        <div style={ROW}>
          <span style={LABEL}>Message history</span>
          <select
            value={String(historyLimit)}
            onChange={e => {
              const v = e.target.value;
              setHistoryLimit(v === 'all' ? 'all' : Number(v));
            }}
            style={{ ...INPUT_BASE, cursor: 'pointer' }}
          >
            {HISTORY_OPTIONS.map(opt => (
              <option key={String(opt.value)} value={String(opt.value)}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

      </div>

      {/* Footer — Save */}
      <div style={{
        padding: '10px 12px',
        borderTop: '1px solid #2a2a4a',
        flexShrink: 0,
      }}>
        <button
          onClick={handleSave}
          style={{
            width: '100%',
            background: saved ? '#2a4a2a' : '#7c6af7',
            border: 'none',
            borderRadius: 5,
            color: '#fff',
            cursor: 'pointer',
            fontSize: 13,
            fontWeight: 600,
            padding: '7px 0',
            transition: 'background 0.2s',
          }}
        >
          {saved ? '✓ Saved' : 'Save'}
        </button>
      </div>
    </div>
  );
}
