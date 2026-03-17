/**
 * UpdateJobControls — inline controls to update a job's status and assignee.
 * Shown in the JobThread header. Uses PATCH /api/jobs/{id}.
 */
import { useState, useCallback } from 'react';
import { updateJobApi } from '../../services/api';
import type { Job, JobStatus } from '../../types';

const STATUS_OPTIONS: JobStatus[] = ['open', 'done', 'archived'];
const STATUS_COLORS: Record<string, string> = {
  open:     '#7c6af7',
  done:     '#22c55e',
  archived: '#6b7280',
};

interface Props {
  job: Job;
}

export function UpdateJobControls({ job }: Props) {
  const [editingAssignee, setEditingAssignee] = useState(false);
  const [assigneeInput, setAssigneeInput] = useState(job.assignee ?? '');
  const [saving, setSaving] = useState(false);

  const handleStatusChange = useCallback(async (status: string) => {
    setSaving(true);
    try {
      await updateJobApi(job.id, { status });
      // WS broadcast will update the store
    } catch { /* ignore — WS may still update */ }
    finally { setSaving(false); }
  }, [job.id]);

  const handleAssigneeSave = useCallback(async () => {
    setSaving(true);
    try {
      await updateJobApi(job.id, { assignee: assigneeInput.trim() });
      setEditingAssignee(false);
    } catch { /* ignore */ }
    finally { setSaving(false); }
  }, [job.id, assigneeInput]);

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 10,
      padding: '10px 16px 12px',
      borderBottom: '1px solid #2a2a4a',
      flexShrink: 0,
      flexWrap: 'wrap',
      background: '#101626',
    }}>
      {/* Status selector */}
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {STATUS_OPTIONS.map(s => (
          <button
            key={s}
            onClick={() => handleStatusChange(s)}
            disabled={saving}
            style={{
              background: job.status === s ? `${STATUS_COLORS[s]}22` : 'transparent',
              border: `1px solid ${job.status === s ? STATUS_COLORS[s] : '#2a2a4a'}`,
              borderRadius: 999,
              color: job.status === s ? STATUS_COLORS[s] : '#55556a',
              cursor: saving ? 'default' : 'pointer',
              fontSize: 11,
              fontWeight: job.status === s ? 700 : 400,
              padding: '5px 10px',
              textTransform: 'capitalize',
              opacity: saving ? 0.6 : 1,
            }}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Assignee */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 'auto', minWidth: 0 }}>
        {editingAssignee ? (
          <>
            <input
              autoFocus
              value={assigneeInput}
              onChange={e => setAssigneeInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleAssigneeSave(); if (e.key === 'Escape') setEditingAssignee(false); }}
              placeholder="Assignee"
              style={{
                background: '#1a1a2e', border: '1px solid #2a2a4a',
                borderRadius: 999, padding: '6px 10px', color: '#e8e8f0',
                fontSize: 12, outline: 'none', width: 120,
              }}
            />
            <button onClick={handleAssigneeSave} disabled={saving}
              style={{ background: '#7c6af7', border: 'none', borderRadius: 999, padding: '6px 10px', color: '#fff', cursor: 'pointer', fontSize: 11, fontWeight: 700 }}>
              Save
            </button>
            <button onClick={() => setEditingAssignee(false)}
              style={{ background: 'none', border: 'none', color: '#55556a', cursor: 'pointer', fontSize: 13 }}>
              ✕
            </button>
          </>
        ) : (
          <button
            onClick={() => { setAssigneeInput(job.assignee ?? ''); setEditingAssignee(true); }}
            style={{
              background: '#161b30', border: '1px solid #2a2a4a',
              borderRadius: 999, padding: '6px 10px', color: '#b7bdd7',
              cursor: 'pointer', fontSize: 11, maxWidth: '100%',
            }}
          >
            {job.assignee ? `@${job.assignee}` : 'Assign'}
          </button>
        )}
      </div>
    </div>
  );
}
