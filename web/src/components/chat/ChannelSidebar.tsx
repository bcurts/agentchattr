/**
 * ChannelSidebar — Phase 4
 *
 * - Unread badges per channel
 * - "+ Add" button opens inline create dialog
 * - Channel creation sends WS event `channel_create`
 * - Active channel persisted to localStorage (handled in chatStore)
 * - channel_renamed handled in useWebSocket (renames refs in store)
 *
 * Channel name rules (exact backend contract, app.py:60-61):
 *   _CHANNEL_NAME_RE = ^[a-z0-9][a-z0-9\-]{0,19}$  (1–20 chars, starts with letter/digit)
 *   MAX_CHANNELS = 8
 */
import { useState, useRef, useEffect } from 'react';
import { useChatStore } from '../../stores/chatStore';
import { channelCreatePayload } from '../../services/api';

// Must exactly mirror backend: ^[a-z0-9][a-z0-9\-]{0,19}$
// i.e. starts with a lowercase letter or digit, then 0–19 more lowercase letters/digits/hyphens
const CHANNEL_NAME_RE = /^[a-z0-9][a-z0-9-]{0,19}$/;
const MAX_CHANNELS = 8;

interface Props {
  channels?: string[];
  onSend?: (payload: object) => void;
}

export function ChannelSidebar({ channels = ['general'], onSend }: Props) {
  const currentChannel = useChatStore(s => s.currentChannel);
  const setCurrentChannel = useChatStore(s => s.setCurrentChannel);
  const unreadCounts = useChatStore(s => s.unreadCounts);

  const atMax = channels.length >= MAX_CHANNELS;

  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus input when dialog opens
  useEffect(() => {
    if (showCreate) {
      setNewName('');
      setError('');
      inputRef.current?.focus();
    }
  }, [showCreate]);

  function handleCreate() {
    const name = newName.trim().toLowerCase();
    if (channels.length >= MAX_CHANNELS) {
      setError(`Max ${MAX_CHANNELS} channels reached`);
      return;
    }
    if (!CHANNEL_NAME_RE.test(name)) {
      setError('Start with a letter/digit, lowercase + hyphens, max 20 chars');
      return;
    }
    if (channels.includes(name)) {
      setError('Channel already exists');
      return;
    }
    onSend?.(channelCreatePayload(name));
    setShowCreate(false);
    setNewName('');
    setError('');
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') handleCreate();
    if (e.key === 'Escape') setShowCreate(false);
  }

  return (
    <div style={{
      width: 200,
      background: '#0d0d1a',
      borderRight: '1px solid #2a2a4a',
      display: 'flex',
      flexDirection: 'column',
      padding: '12px 0',
      flexShrink: 0,
    }}>
      {/* Header row */}
      <div style={{
        padding: '0 12px 8px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <span style={{
          fontSize: 11,
          fontWeight: 700,
          color: '#55556a',
          textTransform: 'uppercase',
          letterSpacing: 1,
        }}>
          Channels
        </span>
        <button
          onClick={() => !atMax && setShowCreate(v => !v)}
          title={atMax ? `Max ${MAX_CHANNELS} channels reached` : 'Add channel'}
          disabled={atMax}
          style={{
            background: showCreate ? '#1f2b47' : 'transparent',
            border: 'none',
            color: atMax ? '#3a3a5a' : (showCreate ? '#7c6af7' : '#55556a'),
            cursor: atMax ? 'not-allowed' : 'pointer',
            fontSize: 16,
            lineHeight: 1,
            padding: '0 2px',
            borderRadius: 3,
            display: 'flex',
            alignItems: 'center',
          }}
        >
          +
        </button>
      </div>

      {/* Create channel inline dialog */}
      {showCreate && (
        <div style={{
          margin: '0 8px 8px',
          background: '#14142a',
          border: '1px solid #2a2a4a',
          borderRadius: 6,
          padding: '8px',
        }}>
          <div style={{ fontSize: 11, color: '#8888aa', marginBottom: 4 }}>
            New channel
          </div>
          <input
            ref={inputRef}
            value={newName}
            onChange={e => { setNewName(e.target.value); setError(''); }}
            onKeyDown={handleKeyDown}
            placeholder="e.g. design"
            style={{
              width: '100%',
              background: '#0f0f17',
              border: `1px solid ${error ? '#e05' : '#2a2a4a'}`,
              borderRadius: 4,
              color: '#e8e8f0',
              fontSize: 12,
              padding: '4px 6px',
              outline: 'none',
              boxSizing: 'border-box',
            }}
          />
          {error && (
            <div style={{ fontSize: 10, color: '#e05', marginTop: 3 }}>{error}</div>
          )}
          <div style={{ display: 'flex', gap: 4, marginTop: 6 }}>
            <button
              onClick={handleCreate}
              style={{
                flex: 1,
                background: '#7c6af7',
                border: 'none',
                borderRadius: 4,
                color: '#fff',
                cursor: 'pointer',
                fontSize: 11,
                padding: '4px 0',
              }}
            >
              Create
            </button>
            <button
              onClick={() => setShowCreate(false)}
              style={{
                flex: 1,
                background: 'transparent',
                border: '1px solid #2a2a4a',
                borderRadius: 4,
                color: '#8888aa',
                cursor: 'pointer',
                fontSize: 11,
                padding: '4px 0',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Channel list */}
      {channels.map(ch => {
        const unread = unreadCounts[ch] ?? 0;
        const isActive = currentChannel === ch;
        return (
          <button
            key={ch}
            onClick={() => setCurrentChannel(ch)}
            style={{
              background: isActive ? '#1f2b47' : 'transparent',
              border: 'none',
              color: isActive ? '#e8e8f0' : '#8888aa',
              cursor: 'pointer',
              padding: '10px 12px',
              textAlign: 'left',
              fontSize: 13,
              borderRadius: 0,
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              borderLeft: isActive ? '2px solid #7c6af7' : '2px solid transparent',
              justifyContent: 'space-between',
              minHeight: 44,
              width: '100%',
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
              <span style={{ opacity: 0.5, flexShrink: 0 }}>#</span>
              <span style={{
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}>
                {ch}
              </span>
            </span>
            {unread > 0 && !isActive && (
              <span style={{
                background: '#7c6af7',
                color: '#fff',
                fontSize: 10,
                fontWeight: 700,
                borderRadius: 8,
                padding: '1px 5px',
                minWidth: 16,
                textAlign: 'center',
                flexShrink: 0,
              }}>
                {unread > 99 ? '99+' : unread}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
