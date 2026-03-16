/**
 * MentionAutocomplete — Phase 6
 *
 * Dropdown for @mention completion in MessageInput.
 * Appears when the user types "@" followed by 0+ characters.
 *
 * Props:
 *   query     — text after "@", used to filter candidates
 *   onSelect  — called with the chosen agent name (no "@" prefix)
 *   onDismiss — called on Escape or blur without selection
 *
 * Keyboard:
 *   ↑ / ↓    — move selection
 *   Enter / Tab — confirm selection
 *   Escape   — dismiss
 *
 * Parent component owns visibility; unmount to hide.
 */
import { useEffect, useRef, useState } from 'react';
import { useAgentStore } from '../../stores/agentStore';

interface Props {
  query: string;
  disabled?: boolean;
  onSelect: (name: string) => void;
  onDismiss: () => void;
}

/** Build the ordered candidate list from all known agent names */
function useCandidates(query: string): string[] {
  const agents    = useAgentStore(s => s.agents);
  const baseColors = useAgentStore(s => s.baseColors);
  const presence  = useAgentStore(s => s.presence);

  // Union of all known names across three sources
  const allNames = Array.from(new Set([
    ...Object.keys(agents),
    ...Object.keys(baseColors),
    ...Object.keys(presence),
  ])).sort();

  const q = query.toLowerCase();
  return q
    ? allNames.filter(n => n.toLowerCase().includes(q))
    : allNames;
}

export function MentionAutocomplete({ query, disabled = false, onSelect, onDismiss }: Props) {
  const candidates = useCandidates(query);
  const getColor   = useAgentStore(s => s.getColor);
  const getLabel   = useAgentStore(s => s.getLabel);
  const presence   = useAgentStore(s => s.presence);

  const [activeIdx, setActiveIdx] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  // Reset selection when candidates change
  useEffect(() => setActiveIdx(0), [query]);

  // Keyboard handler — attached to window so it intercepts before textarea
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (disabled || e.isComposing || (e as KeyboardEvent & { keyCode?: number }).keyCode === 229) {
        return;
      }

      // When no candidates: only Escape dismisses; everything else falls through
      // (Enter will reach MessageInput and send the message normally).
      if (candidates.length === 0) {
        if (e.key === 'Escape') {
          e.preventDefault();
          onDismiss();
        }
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIdx(i => Math.min(i + 1, candidates.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIdx(i => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        onSelect(candidates[activeIdx]);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        onDismiss();
      }
    }
    window.addEventListener('keydown', onKeyDown, true); // capture phase
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [activeIdx, candidates, disabled, onDismiss, onSelect]);

  // Scroll active item into view
  useEffect(() => {
    const el = listRef.current?.querySelector(`[data-idx="${activeIdx}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: 'nearest' });
  }, [activeIdx]);

  if (candidates.length === 0) {
    return (
      <div style={dropdownStyle}>
        <div style={{ padding: '8px 10px', fontSize: 12, color: '#55556a' }}>
          No agents match "{query}"
        </div>
      </div>
    );
  }

  return (
    <div style={dropdownStyle} ref={listRef}>
      {candidates.map((name, idx) => {
        const color    = getColor(name);
        const label    = getLabel(name);
        const pres     = presence[name];
        const isOnline = pres?.available;
        const isBusy   = pres?.busy;
        const isActive = idx === activeIdx;

        return (
          <div
            key={name}
            data-idx={idx}
            onMouseEnter={() => setActiveIdx(idx)}
            onClick={() => onSelect(name)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '5px 10px',
              cursor: 'pointer',
              background: isActive ? '#1f2b47' : 'transparent',
              borderLeft: isActive ? `2px solid ${color}` : '2px solid transparent',
              transition: 'background 0.1s',
            }}
          >
            {/* Color dot */}
            <span style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: color,
              flexShrink: 0,
              opacity: isOnline ? 1 : 0.35,
            }} />

            {/* Name + label */}
            <span style={{ flex: 1, minWidth: 0 }}>
              <span style={{ fontSize: 13, color: isActive ? '#e8e8f0' : '#c0c0d8', fontWeight: 500 }}>
                @{name}
              </span>
              {label !== name && (
                <span style={{ fontSize: 11, color: '#55556a', marginLeft: 5 }}>
                  {label}
                </span>
              )}
            </span>

            {/* Status badge */}
            {pres && (
              <span style={{
                fontSize: 9,
                fontWeight: 700,
                borderRadius: 4,
                padding: '1px 4px',
                textTransform: 'uppercase',
                letterSpacing: 0.4,
                color: isBusy ? '#f0a040' : isOnline ? '#4caf82' : '#55556a',
                background: isBusy ? '#2a1e00' : isOnline ? '#0a2a1a' : '#1a1a2e',
              }}>
                {isBusy ? 'busy' : isOnline ? 'online' : 'offline'}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

const dropdownStyle: React.CSSProperties = {
  position: 'absolute',
  bottom: '100%',
  left: 0,
  right: 0,
  background: '#14142a',
  border: '1px solid #2a2a4a',
  borderRadius: 8,
  boxShadow: '0 -4px 16px rgba(0,0,0,0.4)',
  maxHeight: 220,
  overflowY: 'auto',
  zIndex: 50,
  marginBottom: 4,
};
