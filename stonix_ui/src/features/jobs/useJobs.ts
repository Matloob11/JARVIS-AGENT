import { useCallback, useState } from 'react';
import { toEpochMs } from '@/shared/contracts/events';
import { useSocketEvent } from '@/shared/realtime/subscriptions';
import { normalizeToolLog } from '@/features/tools/useToolLogs';

export type JobStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled';

export interface JobArtifact {
  kind?: string;
  path?: string;
  name?: string;
}

export interface JobEntry {
  job_id: string;
  tool: string;
  title: string;
  status: JobStatus;
  percent: number | null;
  message?: string;
  artifact?: JobArtifact;
  ts: number;
}

const MAX_JOBS = 20;

function normalizeJobStatus(raw: unknown): JobStatus {
  const s = String(raw ?? 'running').toLowerCase();
  if (s === 'queued') return 'queued';
  if (s === 'done' || s === 'success') return 'done';
  if (s === 'error' || s === 'failed') return 'error';
  if (s === 'cancelled' || s === 'canceled') return 'cancelled';
  return 'running';
}

function normalizeJobProgress(raw: unknown): JobEntry | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const job_id = String(data.job_id ?? data.id ?? '').trim();
  if (!job_id) return null;
  const percentRaw = data.percent ?? data.progress;
  const percent =
    typeof percentRaw === 'number' && Number.isFinite(percentRaw)
      ? Math.round(percentRaw)
      : null;
  const artifact = data.artifact;
  return {
    job_id,
    tool: String(data.tool ?? 'job'),
    title: String(data.title ?? data.action ?? data.message ?? data.tool ?? 'Job'),
    status: normalizeJobStatus(data.status),
    percent,
    message: data.message != null ? String(data.message) : undefined,
    artifact:
      artifact && typeof artifact === 'object'
        ? (artifact as JobArtifact)
        : undefined,
    ts: toEpochMs(data.ts ?? data.timestamp) ?? Date.now(),
  };
}

function jobFromToolUpdate(raw: unknown): JobEntry | null {
  const toolRow = normalizeToolLog(raw);
  if (!toolRow) return null;
  if (toolRow.phase !== 'progress') return null;
  return {
    job_id: toolRow.id,
    tool: toolRow.tool,
    title: toolRow.action || toolRow.details || toolRow.tool,
    status: normalizeJobStatus(toolRow.status),
    percent: toolRow.progress ?? null,
    message: toolRow.details,
    ts: toolRow.ts,
  };
}

function sortNewestFirst(rows: JobEntry[]): JobEntry[] {
  return [...rows].sort((a, b) => b.ts - a.ts);
}

function upsertJob(prev: JobEntry[], next: JobEntry): JobEntry[] {
  const without = prev.filter(row => row.job_id !== next.job_id);
  return sortNewestFirst([next, ...without]).slice(0, MAX_JOBS);
}

export function useJobs(emitCommand: (type: string, payload?: unknown) => void) {
  const [jobs, setJobs] = useState<JobEntry[]>([]);

  const upsert = useCallback((raw: unknown) => {
    const entry = normalizeJobProgress(raw) ?? jobFromToolUpdate(raw);
    if (!entry) return;
    setJobs(prev => upsertJob(prev, entry));
  }, []);

  const seedFromInit = useCallback((state: Record<string, unknown>) => {
    const list = state.jobs;
    if (!Array.isArray(list) || list.length === 0) return;
    const seeded = list
      .map(raw => normalizeJobProgress(raw))
      .filter((row): row is JobEntry => row != null);
    if (!seeded.length) return;
    setJobs(prev => {
      const byId = new Map<string, JobEntry>();
      for (const row of sortNewestFirst([...seeded, ...prev])) {
        if (!byId.has(row.job_id)) byId.set(row.job_id, row);
      }
      return sortNewestFirst([...byId.values()]).slice(0, MAX_JOBS);
    });
  }, []);

  useSocketEvent('tool_progress', (payload: unknown) => upsert(payload));
  useSocketEvent('tool_update', (payload: unknown) => {
    const fromTool = jobFromToolUpdate(payload);
    if (fromTool) upsert(fromTool);
  });

  const cancelJob = useCallback(
    (jobId: string) => {
      emitCommand('job_cancel', { job_id: jobId });
    },
    [emitCommand],
  );

  const openJobArtifact = useCallback(
    (jobId: string) => {
      emitCommand('open_job_artifact', { job_id: jobId });
    },
    [emitCommand],
  );

  return {
    jobs,
    seedJobsFromInit: seedFromInit,
    upsertJob: upsert,
    cancelJob,
    openJobArtifact,
  };
}
