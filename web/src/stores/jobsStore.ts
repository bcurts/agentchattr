/**
 * Jobs store — kanban board + job thread messages
 *
 * WS job events (api-contract-strict.md):
 *   create        → data: Job
 *   update        → data: Job (full job object, status/title/assignee/order changed)
 *   message       → data: {job_id, message: JobMessage}
 *   message_delete→ data: {job_id, message_id}
 *   delete        → data: Job
 */
import { create } from 'zustand';
import type { Job, JobStatus, JobMessage } from '../types';

interface JobsState {
  jobs: Job[];
  activeJobId: number | null;

  // Bulk set (from WS `jobs` snapshot)
  setJobs: (jobs: Job[]) => void;

  // Individual job CRUD (from WS `job` events)
  addJob: (job: Job) => void;
  updateJob: (id: number, patch: Partial<Job>) => void;
  removeJob: (id: number) => void;

  // Job thread message operations
  addJobMessage: (jobId: number, message: JobMessage) => void;
  deleteJobMessage: (jobId: number, messageId: number) => void;

  // UI state
  setActiveJobId: (id: number | null) => void;

  // Derived
  byStatus: (status: JobStatus) => Job[];
  getActiveJob: () => Job | null;
}

export const useJobsStore = create<JobsState>((set, get) => ({
  jobs: [],
  activeJobId: null,

  setJobs: (jobs) => set({ jobs }),

  addJob: (job) =>
    set((s) => {
      // Avoid duplicates if snapshot + live event both arrive
      if (s.jobs.find(j => j.id === job.id)) return s;
      return { jobs: [...s.jobs, job] };
    }),

  updateJob: (id, patch) =>
    set((s) => ({
      jobs: s.jobs.map((j) => (j.id === id ? { ...j, ...patch } : j)),
    })),

  removeJob: (id) =>
    set((s) => ({
      jobs: s.jobs.filter((j) => j.id !== id),
      // Clear activeJobId if the deleted job was active
      activeJobId: s.activeJobId === id ? null : s.activeJobId,
    })),

  addJobMessage: (jobId, message) =>
    set((s) => ({
      jobs: s.jobs.map((j) =>
        j.id === jobId
          ? { ...j, messages: [...j.messages, message] }
          : j
      ),
    })),

  deleteJobMessage: (jobId, messageId) =>
    set((s) => ({
      jobs: s.jobs.map((j) =>
        j.id === jobId
          ? { ...j, messages: j.messages.filter((m) => m.id !== messageId) }
          : j
      ),
    })),

  setActiveJobId: (id) => set({ activeJobId: id }),

  byStatus: (status) => get().jobs.filter((j) => j.status === status),

  getActiveJob: () => {
    const { jobs, activeJobId } = get();
    return activeJobId !== null ? (jobs.find(j => j.id === activeJobId) ?? null) : null;
  },
}));
