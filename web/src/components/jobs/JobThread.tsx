/**
 * JobThread — shows messages inside a job + send-message input.
 * Appears when a job is selected (activeJobId set).
 *
 * FIX: job thread messages are sent via REST POST /api/jobs/{id}/messages,
 * NOT via WebSocket. WS is only used to receive broadcast updates.
 */
import { useRef, useEffect, useState, useCallback } from 'react';
import { useJobsStore } from '../../stores/jobsStore';
import { useSettingsStore } from '../../stores/settingsStore';
import { postJobMessage } from '../../services/api';
import { UpdateJobControls } from './UpdateJobControls';
import { JobMessageItem } from './JobMessageItem';

export function JobThread() {
  const job = useJobsStore(s => s.getActiveJob());
  const setActiveJobId = useJobsStore(s => s.setActiveJobId);
  const username = useSettingsStore(s => s.settings.username);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isComposing, setIsComposing] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [job?.messages.length, job?.id]);

  const handleSend = useCallback(async () => {
    const trimmed = text.trim();
    if (!trimmed || !job) return;
    setSending(true);
    setError(null);
    try {
      // REST POST — backend broadcasts via WS, store updates via job action=message
      await postJobMessage(job.id, { text: trimmed, sender: username });
      setText('');
    } catch (e: any) {
      setError(e.message ?? 'Failed to send');
    } finally {
      setSending(false);
    }
  }, [text, job, username]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const nativeEvent = e.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
    if (isComposing || nativeEvent.isComposing || nativeEvent.keyCode === 229) {
      return;
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!job) return null;

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: '#0d0d1a',
      borderLeft: '1px solid #2a2a4a',
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 14px',
        borderBottom: '1px solid #2a2a4a',
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        flexShrink: 0,
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: '#e8e8f0', marginBottom: 2 }}>
            {job.title}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {job.anchor_msg_id && (
              <span style={{
                fontSize: 10, color: '#7c6af7', background: '#7c6af722',
                padding: '0 4px', borderRadius: 3, fontWeight: 700,
              }}>
                ANCHOR: #{job.anchor_msg_id}
              </span>
            )}
            {job.body && (
              <div style={{
                fontSize: 11, color: '#8888aa',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {job.body}
              </div>
            )}
          </div>
        </div>
        <button
          onClick={() => setActiveJobId(null)}
          style={{
            background: 'none', border: 'none', color: '#55556a',
            cursor: 'pointer', fontSize: 16, padding: 0, flexShrink: 0,
          }}
          title="Close thread"
        >
          ✕
        </button>
      </div>

      {/* Status / Assignee controls */}
      <UpdateJobControls job={job} />

      {/* Messages */}
      <div style={{
        flex: 1, overflowY: 'auto', padding: '8px 0',
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>
        {job.messages.length === 0 && (
          <div style={{
            textAlign: 'center', color: '#55556a',
            fontSize: 12, padding: '24px 0',
          }}>
            No messages yet in this job thread
          </div>
        )}
        {job.messages.map(msg => (
          <JobMessageItem key={msg.id} message={msg} />
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Error */}
      {error && (
        <div style={{ padding: '4px 14px', fontSize: 12, color: '#ef4444' }}>
          {error}
        </div>
      )}

      {/* Input */}
      <div style={{ padding: '8px 10px 10px', borderTop: '1px solid #2a2a4a', flexShrink: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'flex-end', gap: 6,
          background: '#1a1a2e', border: '1px solid #2a2a4a',
          borderRadius: 6, padding: '4px 8px',
        }}>
          <textarea
            value={text}
            onChange={e => setText(e.target.value)}
            onCompositionStart={() => setIsComposing(true)}
            onCompositionEnd={e => {
              setIsComposing(false);
              setText(e.currentTarget.value);
            }}
            onKeyDown={handleKeyDown}
            placeholder="Reply in thread…"
            rows={1}
            disabled={sending}
            style={{
              flex: 1, background: 'transparent', border: 'none', outline: 'none',
              resize: 'none', color: '#e8e8f0', fontSize: 13, lineHeight: 1.5,
              fontFamily: 'inherit', minHeight: 20, opacity: sending ? 0.6 : 1,
            }}
          />
          <button
            onClick={handleSend}
            disabled={!text.trim() || sending}
            style={{
              background: text.trim() && !sending ? '#7c6af7' : '#2a2a4a',
              border: 'none', borderRadius: 4, padding: '2px 8px',
              color: text.trim() && !sending ? '#fff' : '#55556a',
              cursor: text.trim() && !sending ? 'pointer' : 'default',
              fontSize: 12, flexShrink: 0,
            }}
          >
            {sending ? '…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
}
