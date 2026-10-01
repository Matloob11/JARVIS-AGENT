import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from '@/toast';
import { toEpochMs } from '@/shared/contracts/events';
import { getSocket } from '@/shared/realtime/socket';

export type ToolLogStatus =
  | 'running'
  | 'success'
  | 'error'
  | 'blocked'
  | 'denied'
  | 'timeout'
  | 'cancelled'
  | 'needs_confirmation'
  | 'degraded';

export interface ToolLogEntry {
  id: string;
  tool: string;
  action?: string;
  status: ToolLogStatus;
  details?: string;
  result_summary?: string;
  progress?: number;
  duration_ms?: number;
  risk?: string;
  phase?: string;
  ts: number;
}

export interface ToolLogLastError {
  tool: string;
  message: string;
  ts: number;
}

const MAX_ROWS = 200;

const ERROR_STATUSES: ToolLogStatus[] = ['error', 'blocked', 'denied', 'timeout', 'cancelled'];

function normalizeStatus(raw: unknown): ToolLogStatus {
  const s = String(raw ?? 'running').toLowerCase();
  if (s === 'running' || s === 'started') return 'running';
  if (s === 'success' || s === 'done') return 'success';
  if (s === 'not_found') return 'error';
  if (
    s === 'error' ||
    s === 'blocked' ||
    s === 'denied' ||
    s === 'timeout' ||
    s === 'cancelled' ||
    s === 'needs_confirmation' ||
    s === 'degraded'
  ) {
    return s;
  }
  return 'running';
}

function rowId(data: Record<string, unknown>): string {
  if (data.id != null && String(data.id).trim()) return String(data.id);
  const ts = toEpochMs(data.ts ?? data.timestamp) ?? Date.now();
  const tool = String(data.tool ?? 'tool');
  return `legacy-${ts}-${tool}`;
}

export function normalizeToolLog(raw: unknown): ToolLogEntry | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const tool = String(data.tool ?? data.name ?? '').trim();
  if (!tool) return null;

  const ts = toEpochMs(data.ts ?? data.timestamp) ?? Date.now();
  const progress =
    typeof data.progress === 'number' && Number.isFinite(data.progress)
      ? Math.round(data.progress)
      : undefined;
  const duration_ms =
    typeof data.duration_ms === 'number' && Number.isFinite(data.duration_ms)
      ? Math.round(data.duration_ms)
      : undefined;

  return {
    id: rowId(data),
    tool,
    action: data.action != null ? String(data.action) : undefined,
    status: normalizeStatus(data.status),
    details: data.details != null ? String(data.details) : undefined,
    result_summary:
      data.result_summary != null
        ? String(data.result_summary)
        : data.result != null
          ? String(data.result)
          : undefined,
    progress,
    duration_ms,
    risk: data.risk != null ? String(data.risk) : undefined,
    phase: data.phase != null ? String(data.phase) : undefined,
    ts,
  };
}

function sortNewestFirst(rows: ToolLogEntry[]): ToolLogEntry[] {
  return [...rows].sort((a, b) => b.ts - a.ts);
}

function upsertRow(prev: ToolLogEntry[], next: ToolLogEntry): ToolLogEntry[] {
  const without = prev.filter(row => row.id !== next.id);
  return sortNewestFirst([next, ...without]).slice(0, MAX_ROWS);
}

function computeLastError(rows: ToolLogEntry[]): ToolLogLastError | null {
  for (const row of sortNewestFirst(rows)) {
    if (!ERROR_STATUSES.includes(row.status)) continue;
    const message = row.result_summary || row.details || row.status;
    return { tool: row.tool, message, ts: row.ts };
  }
  return null;
}

export function useToolLogs() {
  const [rows, setRows] = useState<ToolLogEntry[]>([]);
  const mediumToasted = useRef(new Set<string>());

  const upsert = useCallback((raw: unknown) => {
    const entry = normalizeToolLog(raw);
    if (!entry) return;
    setRows(prev => upsertRow(prev, entry));

    const risk = entry.risk?.toLowerCase();
    if (
      risk === 'medium' &&
      entry.status === 'running' &&
      !mediumToasted.current.has(entry.id)
    ) {
      mediumToasted.current.add(entry.id);
      toast(`${entry.tool} chal raha hai`, { icon: '🔧' });
    }
  }, []);

  const seedFromInit = useCallback((state: Record<string, unknown>) => {
    const list = state.tool_logs;
    if (!Array.isArray(list) || list.length === 0) return;
    const seeded = list
      .map(raw => normalizeToolLog(raw))
      .filter((row): row is ToolLogEntry => row != null);
    if (!seeded.length) return;
    setRows(prev => {
      const byId = new Map<string, ToolLogEntry>();
      for (const row of sortNewestFirst([...seeded, ...prev])) {
        if (!byId.has(row.id)) byId.set(row.id, row);
      }
      return sortNewestFirst([...byId.values()]).slice(0, MAX_ROWS);
    });
  }, []);

  useEffect(() => {
    const socket = getSocket();
    const onToolUpdate = (payload: unknown) => upsert(payload);
    socket.on('tool_update', onToolUpdate);
    return () => {
      socket.off('tool_update', onToolUpdate);
    };
  }, [upsert]);

  const runningCount = useMemo(
    () => rows.filter(row => row.status === 'running').length,
    [rows],
  );

  const lastError = useMemo(() => computeLastError(rows), [rows]);

  return {
    toolLogs: rows,
    runningCount,
    lastError,
    upsertToolLog: upsert,
    seedToolLogsFromInit: seedFromInit,
  };
}
