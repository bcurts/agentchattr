/**
 * JobCard — compact card showing a job's title, status, assignee, message count.
 * Clicking selects the job (opens thread).
 */
import { useJobsStore } from '../../stores/jobsStore';
import { AgentAvatar } from '../agents/AgentAvatar';
import type { Job } from '../../types';

const STATUS_COLORS: Record<string, string> = {
  open:     '#7c6af7',
  done:     '#22c55e',
  archived: '#6b7280',
};

const STATUS_LABELS: Record<string, string> = {
  open:     'Open',
  done:     'Done',
  archived: 'Archived',
};

interface Props {
  job: Job;
}

export function JobCard({ job }: Props) {
  const activeJobId = useJobsStore(s => s.activeJobId);
  const setActiveJobId = useJobsStore(s => s.setActiveJobId);

  const isActive = activeJobId === job.id;

  return (
    <div
      onClick={() => setActiveJobId(isActive ? null : job.id)}
      style={{
        background: isActive ? '#1f2b47' : '#16213e',
        border: `1px solid ${isActive ? '#7c6af7' : '#2a2a4a'}`,
        borderRadius: 6,
        padding: '8px 10px',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        transition: 'border-color 0.15s, background 0.15s',
      }}
      onMouseEnter={e => { if (!isActive) (e.currentTarget as HTMLDivElement).style.background = '#1a2035'; }}
      onMouseLeave={e => { if (!isActive) (e.currentTarget as HTMLDivElement).style.background = '#16213e'; }}
    >
      {/* Title */}
      <div style={{
        fontSize: 13,
        fontWeight: 600,
        color: '#e8e8f0',
        whiteSpace: 'nowrap',
        overflow: 'hidden',
        textOverflow: 'ellipsis',
      }}>
        {job.title}
      </div>

      {/* Meta row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        {/* Status badge */}
        <span style={{
          fontSize: 10,
          fontWeight: 700,
          color: STATUS_COLORS[job.status] ?? '#8888aa',
          background: `${STATUS_COLORS[job.status] ?? '#8888aa'}22`,
          padding: '1px 5px',
          borderRadius: 3,
          textTransform: 'uppercase',
          letterSpacing: 0.5,
        }}>
          {STATUS_LABELS[job.status] ?? job.status}
        </span>

        {/* Message count */}
        {job.messages.length > 0 && (
          <span style={{ fontSize: 11, color: '#8888aa' }}>
            💬 {job.messages.length}
          </span>
        )}

        {/* Assignee avatar */}
        {job.assignee && (
          <div style={{ marginLeft: 'auto' }}>
            <AgentAvatar name={job.assignee} size="sm" showHat={false} />
          </div>
        )}
      </div>
    </div>
  );
}
