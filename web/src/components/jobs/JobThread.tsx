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
import { deleteJob, postJobMessage } from '../../services/api';
import { UpdateJobControls } from './UpdateJobControls';
import { JobMessageItem } from './JobMessageItem';
import { AlertDialog } from '../ui/AlertDialog';

export function JobThread() {
  const job = useJobsStore(s => s.getActiveJob());
  const setActiveJobId = useJobsStore(s => s.setActiveJobId);
  const username = useSettingsStore(s => s.settings.username);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
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

  const handleDelete = useCallback(async () => {
    if (!job || deleting) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteJob(job.id, true);
      setActiveJobId(null);
    } catch (e: any) {
      setError(e.message ?? 'Failed to delete thread');
    } finally {
      setDeleting(false);
      setShowDeleteDialog(false);
    }
  }, [job, deleting, setActiveJobId]);

  if (!job) return null;

  const statusColor =
    job.status === 'done' ? '#22c55e' :
    job.status === 'archived' ? '#94a3b8' :
    '#7c6af7';

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      background: 'linear-gradient(180deg, #0d1324 0%, #0b0f1c 100%)',
      borderLeft: '1px solid #2a2a4a',
    }}>
      {/* Header */}
      <div style={{
        padding: '16px',
        borderBottom: '1px solid #2a2a4a',
        display: 'flex',
        alignItems: 'flex-start',
        gap: 12,
        flexShrink: 0,
        background: 'linear-gradient(180deg, rgba(124,106,247,0.08) 0%, rgba(16,22,38,0.85) 100%)',
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            gap: 12,
            marginBottom: 10,
            flexWrap: 'wrap',
          }}>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{
                fontSize: 11,
                fontWeight: 700,
                color: '#7c6af7',
                letterSpacing: 1,
                textTransform: 'uppercase',
                marginBottom: 6,
              }}>
                Job Detail
              </div>
              <div style={{ fontWeight: 800, fontSize: 18, lineHeight: 1.2, color: '#f4f7ff' }}>
                {job.title}
              </div>
            </div>
            <div style={{
              fontSize: 11,
              fontWeight: 700,
              color: statusColor,
              background: `${statusColor}1f`,
              border: `1px solid ${statusColor}40`,
              padding: '6px 10px',
              borderRadius: 999,
              textTransform: 'uppercase',
            }}>
              {job.status}
            </div>
          </div>

          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flexWrap: 'wrap',
            marginBottom: job.body ? 10 : 0,
          }}>
            <span style={{
              fontSize: 11,
              color: '#9aa3c7',
              background: '#11182b',
              border: '1px solid #2a2a4a',
              padding: '4px 8px',
              borderRadius: 999,
            }}>
              {job.messages.length} message{job.messages.length === 1 ? '' : 's'}
            </span>
            {job.assignee && (
              <span style={{
                fontSize: 11,
                color: '#cfd3ea',
                background: '#11182b',
                border: '1px solid #2a2a4a',
                padding: '4px 8px',
                borderRadius: 999,
              }}>
                Assigned to @{job.assignee}
              </span>
            )}
            {job.anchor_msg_id && (
              <span style={{
                fontSize: 11,
                color: '#7c6af7',
                background: '#7c6af71a',
                border: '1px solid #7c6af740',
                padding: '4px 8px',
                borderRadius: 999,
              }}>
                Anchor #{job.anchor_msg_id}
              </span>
            )}
          </div>

          {job.body && (
            <div style={{
              fontSize: 12,
              color: '#aeb5d3',
              lineHeight: 1.55,
              background: '#0f1526',
              border: '1px solid #202b47',
              borderRadius: 12,
              padding: '10px 12px',
              maxHeight: 110,
              overflowY: 'auto',
              whiteSpace: 'pre-wrap',
            }}>
              {job.body}
            </div>
          )}
        </div>
        <button
          onClick={() => setActiveJobId(null)}
          style={{
            background: '#151b2d',
            border: '1px solid #2a2a4a',
            color: '#7f88aa',
            cursor: 'pointer',
            fontSize: 16,
            width: 36,
            height: 36,
            borderRadius: 999,
            flexShrink: 0,
          }}
          title="Close thread"
        >
          ✕
        </button>
      </div>

      <div style={{
        padding: '0 16px 12px',
        borderBottom: '1px solid #2a2a4a',
        display: 'flex',
        justifyContent: 'flex-end',
        background: 'linear-gradient(180deg, rgba(16,22,38,0.85) 0%, rgba(12,18,34,1) 100%)',
      }}>
        <button
          onClick={() => setShowDeleteDialog(true)}
          disabled={deleting}
          style={{
            background: deleting ? '#3b1720' : '#4a1721',
            border: '1px solid #7f1d1d',
            color: deleting ? '#c08497' : '#fecdd3',
            cursor: deleting ? 'default' : 'pointer',
            fontSize: 12,
            fontWeight: 700,
            borderRadius: 999,
            padding: '8px 12px',
          }}
        >
          {deleting ? 'Deleting…' : 'Delete Thread'}
        </button>
      </div>

      {/* Status / Assignee controls */}
      <UpdateJobControls job={job} />

      {/* Messages */}
      <div style={{
        flex: 1,
        overflowY: 'auto',
        padding: '12px 0 16px',
        display: 'flex', flexDirection: 'column', gap: 2,
      }}>
        {job.messages.length === 0 && (
          <div style={{
            margin: '20px 16px',
            textAlign: 'center',
            color: '#7f88aa',
            fontSize: 12,
            padding: '28px 16px',
            background: '#101626',
            border: '1px dashed #2a2a4a',
            borderRadius: 16,
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
        <div style={{
          margin: '0 16px 12px',
          padding: '10px 12px',
          fontSize: 12,
          color: '#fca5a5',
          background: '#31161b',
          border: '1px solid #7f1d1d',
          borderRadius: 12,
        }}>
          {error}
        </div>
      )}

      {/* Input */}
      <div style={{
        padding: '12px 16px 16px',
        borderTop: '1px solid #2a2a4a',
        flexShrink: 0,
        background: '#0c1222',
      }}>
        <div style={{ fontSize: 11, color: '#7f88aa', marginBottom: 8 }}>
          Reply in this job thread
        </div>
        <div style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 8,
          background: '#141a2d',
          border: '1px solid #2a2a4a',
          borderRadius: 16,
          padding: '8px 10px',
          boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.03)',
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
              flex: 1,
              background: 'transparent',
              border: 'none',
              outline: 'none',
              resize: 'none',
              color: '#e8e8f0',
              fontSize: 13,
              lineHeight: 1.5,
              fontFamily: 'inherit',
              minHeight: 56,
              maxHeight: 160,
              opacity: sending ? 0.6 : 1,
            }}
          />
          <button
            onClick={handleSend}
            disabled={!text.trim() || sending}
            style={{
              background: text.trim() && !sending ? '#7c6af7' : '#2a2a4a',
              border: 'none',
              borderRadius: 999,
              padding: '10px 14px',
              color: text.trim() && !sending ? '#fff' : '#55556a',
              cursor: text.trim() && !sending ? 'pointer' : 'default',
              fontSize: 12,
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            {sending ? '…' : 'Send'}
          </button>
        </div>
      </div>

      <AlertDialog
        open={showDeleteDialog}
        title={job ? `Delete "${job.title}"?` : 'Delete thread?'}
        description={(
          <span>
            This permanently removes the job and its full conversation thread. <span style={{ color: '#fda4af' }}>This cannot be undone.</span>
          </span>
        )}
        confirmLabel="Delete Thread"
        cancelLabel="Keep Thread"
        confirmTone="destructive"
        busy={deleting}
        onCancel={() => !deleting && setShowDeleteDialog(false)}
        onConfirm={handleDelete}
      />
    </div>
  );
}
