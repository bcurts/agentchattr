/**
 * CreateJobForm — inline form to create a new job via POST /api/jobs.
 * WS broadcast will add the new job to the store automatically.
 */
import { useState, useCallback, useEffect } from 'react';
import { useSettingsStore } from '../../stores/settingsStore';
import { createJob } from '../../services/api';

interface Props {
  initialTitle?: string;
  initialBody?: string;
  initialChannel?: string;
  anchorMsgId?: number;
  onCreated?: () => void;
  onCancel?: () => void;
  forceOpen?: boolean;
}

export function CreateJobForm({
  initialTitle = '',
  initialBody = '',
  initialChannel,
  anchorMsgId,
  onCreated,
  onCancel,
  forceOpen = false,
}: Props) {
  const [open, setOpen] = useState(forceOpen);
  const [title, setTitle] = useState(initialTitle);
  const [body, setBody] = useState(initialBody);
  const [assignee, setAssignee] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const defaultChannel = useSettingsStore(s => s.settings.channels[0] ?? 'general');
  const channel = initialChannel || defaultChannel;
  const username = useSettingsStore(s => s.settings.username);

  // Sync state if props change (e.g. promoting different messages)
  useEffect(() => {
    if (forceOpen) {
      setOpen(true);
      setTitle(initialTitle);
      setBody(initialBody);
    }
  }, [forceOpen, initialTitle, initialBody]);

  const handleSubmit = useCallback(async () => {
    const t = title.trim();
    if (!t) return;
    setSubmitting(true);
    setError(null);
    try {
      await createJob({
        title: t,
        body: body.trim(),
        channel,
        created_by: username,
        assignee: assignee.trim(),
        anchor_msg_id: anchorMsgId,
      });
      setTitle('');
      setBody('');
      setAssignee('');
      setOpen(false);
      onCreated?.();
    } catch (e: any) {
      setError(e.message ?? 'Failed to create job');
    } finally {
      setSubmitting(false);
    }
  }, [title, body, channel, username, assignee, anchorMsgId, onCreated]);

  const handleCancel = useCallback(() => {
    setOpen(false);
    setError(null);
    onCancel?.();
  }, [onCancel]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        style={{
          width: '100%',
          background: 'transparent',
          border: '1px dashed #2a2a4a',
          borderRadius: 6,
          color: '#55556a',
          cursor: 'pointer',
          fontSize: 12,
          padding: '6px',
          textAlign: 'center',
          marginBottom: 8,
        }}
      >
        + New Job
      </button>
    );
  }

  return (
    <div style={{
      background: '#16213e',
      border: '1px solid #2a2a4a',
      borderRadius: 6,
      padding: '10px',
      marginBottom: 8,
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
    }}>
      <input
        autoFocus
        value={title}
        onChange={e => setTitle(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && handleSubmit()}
        placeholder="Job title *"
        style={{
          background: '#1a1a2e', border: '1px solid #2a2a4a',
          borderRadius: 4, padding: '5px 8px', color: '#e8e8f0',
          fontSize: 13, outline: 'none', width: '100%',
        }}
      />
      <textarea
        value={body}
        onChange={e => setBody(e.target.value)}
        placeholder="Description (optional)"
        rows={3}
        style={{
          background: '#1a1a2e', border: '1px solid #2a2a4a',
          borderRadius: 4, padding: '5px 8px', color: '#e8e8f0',
          fontSize: 12, outline: 'none', resize: 'none', fontFamily: 'inherit', width: '100%',
        }}
      />
      <input
        value={assignee}
        onChange={e => setAssignee(e.target.value)}
        placeholder="Assignee (optional)"
        style={{
          background: '#1a1a2e', border: '1px solid #2a2a4a',
          borderRadius: 4, padding: '5px 8px', color: '#e8e8f0',
          fontSize: 12, outline: 'none', width: '100%',
        }}
      />
      {error && <div style={{ color: '#ef4444', fontSize: 11 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <button
          onClick={handleCancel}
          style={{
            background: 'transparent', border: '1px solid #2a2a4a',
            borderRadius: 4, padding: '3px 10px', color: '#8888aa',
            cursor: 'pointer', fontSize: 12,
          }}
        >
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={!title.trim() || submitting}
          style={{
            background: title.trim() && !submitting ? '#7c6af7' : '#2a2a4a',
            border: 'none', borderRadius: 4, padding: '3px 12px',
            color: title.trim() && !submitting ? '#fff' : '#55556a',
            cursor: title.trim() && !submitting ? 'pointer' : 'default',
            fontSize: 12,
          }}
        >
          {submitting ? 'Creating…' : 'Create'}
        </button>
      </div>
    </div>
  );
}
