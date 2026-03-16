/**
 * JobsPanel — kanban board: Open | Done | Archived columns.
 * Shown as a slide-over panel or sidebar depending on screen size.
 */
import { useJobsStore } from '../../stores/jobsStore';
import { JobCard } from './JobCard';
import { CreateJobForm } from './CreateJobForm';
import type { JobStatus } from '../../types';

const COLUMNS: { status: JobStatus; label: string; color: string }[] = [
  { status: 'open',     label: 'Open',     color: '#7c6af7' },
  { status: 'done',     label: 'Done',     color: '#22c55e' },
  { status: 'archived', label: 'Archived', color: '#6b7280' },
];

interface Props {
  onClose?: () => void;
}

export function JobsPanel({ onClose }: Props) {
  const byStatus = useJobsStore(s => s.byStatus);

  return (
    <div style={{
      width: 280,
      display: 'flex',
      flexDirection: 'column',
      background: '#0d0d1a',
      borderLeft: '1px solid #2a2a4a',
      overflow: 'hidden',
      flexShrink: 0,
    }}>
      {/* Header */}
      <div style={{
        padding: '10px 14px',
        borderBottom: '1px solid #2a2a4a',
        display: 'flex',
        alignItems: 'center',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: '#e8e8f0' }}>Jobs</span>
        {onClose && (
          <button
            onClick={onClose}
            style={{ marginLeft: 'auto', background: 'none', border: 'none', color: '#55556a', cursor: 'pointer', fontSize: 15 }}
          >
            ✕
          </button>
        )}
      </div>

      {/* Columns (scrollable) */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 10px' }}>
        {/* Create new job */}
        <CreateJobForm />
        {COLUMNS.map(col => {
          const jobs = byStatus(col.status).sort((a, b) => a.sort_order - b.sort_order);
          return (
            <div key={col.status} style={{ marginBottom: 16 }}>
              {/* Column header */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                marginBottom: 6,
                padding: '0 2px',
              }}>
                <span style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: col.color,
                  flexShrink: 0,
                }} />
                <span style={{ fontSize: 11, fontWeight: 700, color: '#8888aa', textTransform: 'uppercase', letterSpacing: 0.8 }}>
                  {col.label}
                </span>
                <span style={{
                  fontSize: 10,
                  color: '#55556a',
                  background: '#1a1a2e',
                  padding: '0 5px',
                  borderRadius: 8,
                  marginLeft: 'auto',
                }}>
                  {jobs.length}
                </span>
              </div>

              {/* Cards */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {jobs.length === 0 && (
                  <div style={{ fontSize: 11, color: '#55556a', padding: '4px 2px', fontStyle: 'italic' }}>
                    No {col.label.toLowerCase()} jobs
                  </div>
                )}
                {jobs.map(job => (
                  <JobCard key={job.id} job={job} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
