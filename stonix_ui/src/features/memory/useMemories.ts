import { useCallback, useState } from 'react';
import { toast } from '@/toast';
import { toEpochMs } from '@/shared/contracts/events';
import { useSocketEvent } from '@/shared/realtime/subscriptions';

export type MemoryRole = 'user' | 'assistant';
export type MemorySource = 'extractor' | 'fact' | 'vector';

export interface MemoryItem {
  id: string;
  content: string;
  role?: MemoryRole;
  source?: MemorySource;
  ts: number;
}

const MAX_ROWS = 200;

function normalizeRole(raw: unknown): MemoryRole | undefined {
  const r = String(raw ?? '').toLowerCase();
  if (r === 'user' || r === 'assistant') return r;
  return undefined;
}

function normalizeSource(raw: unknown): MemorySource | undefined {
  const s = String(raw ?? '').toLowerCase();
  if (s === 'extractor' || s === 'fact' || s === 'vector') return s;
  return undefined;
}

function contentText(raw: unknown): string {
  if (typeof raw === 'string') return raw;
  if (raw && typeof raw === 'object') return JSON.stringify(raw);
  if (raw != null) return String(raw);
  return '';
}

export function normalizeMemory(raw: unknown): MemoryItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const id = data.id != null ? String(data.id).trim() : '';
  if (!id) return null;
  const content = contentText(data.content);
  if (!content.trim()) return null;
  const ts = toEpochMs(data.ts ?? data.timestamp) ?? Date.now();
  return {
    id,
    content,
    role: normalizeRole(data.role),
    source: normalizeSource(data.source),
    ts,
  };
}

function sortNewestFirst(rows: MemoryItem[]): MemoryItem[] {
  return [...rows].sort((a, b) => b.ts - a.ts);
}

function prependRow(prev: MemoryItem[], next: MemoryItem): MemoryItem[] {
  const without = prev.filter(row => row.id !== next.id);
  return sortNewestFirst([next, ...without]).slice(0, MAX_ROWS);
}

function mergeSeed(prev: MemoryItem[], seeded: MemoryItem[]): MemoryItem[] {
  const byId = new Map<string, MemoryItem>();
  for (const row of sortNewestFirst([...seeded, ...prev])) {
    if (!byId.has(row.id)) byId.set(row.id, row);
  }
  return sortNewestFirst([...byId.values()]).slice(0, MAX_ROWS);
}

function backupPathFromCleared(payload: Record<string, unknown>): string | null {
  const backup = payload.backup;
  if (typeof backup === 'string' && backup.trim()) return backup.trim();
  const backupPath = payload.backup_path;
  if (typeof backupPath === 'string' && backupPath.trim()) return backupPath.trim();
  return null;
}

export function useMemories(emitCommand: (type: string, payload?: unknown) => void) {
  const [rows, setRows] = useState<MemoryItem[]>([]);
  const [lastMemorySyncMs, setLastMemorySyncMs] = useState<number | null>(null);

  const applySync = useCallback((raw: unknown) => {
    const entry = normalizeMemory(raw);
    if (!entry) return;
    setLastMemorySyncMs(entry.ts);
    setRows(prev => prependRow(prev, entry));
  }, []);

  const applyCleared = useCallback((raw: unknown) => {
    setRows([]);
    if (!raw || typeof raw !== 'object') return;
    const data = raw as Record<string, unknown>;
    const scopeRaw = data.scope;
    const scope =
      typeof scopeRaw === 'string' && scopeRaw.trim()
        ? scopeRaw.trim().replace(/_/g, ' ')
        : 'memory';
    const path = backupPathFromCleared(data);
    const parts = [`${scope} cleared`];
    if (path) parts.push(`Backup: ${path}`);
    toast(parts.join(' · '), { icon: '💾', duration: 12_000 });
  }, []);

  const seedFromInit = useCallback((state: Record<string, unknown>) => {
    const list = state.memories;
    if (!Array.isArray(list) || list.length === 0) return;
    const seeded = list
      .map(raw => normalizeMemory(raw))
      .filter((row): row is MemoryItem => row != null);
    if (!seeded.length) return;
    setRows(prev => mergeSeed(prev, seeded));
    const newest = seeded.reduce((max, row) => (row.ts > max ? row.ts : max), seeded[0].ts);
    setLastMemorySyncMs(prev => (prev == null || newest > prev ? newest : prev));
  }, []);

  const clearShortTermMemory = useCallback(() => {
    emitCommand('clear_memory', {});
  }, [emitCommand]);

  useSocketEvent('memory_sync', (payload: unknown) => applySync(payload));
  useSocketEvent('memory_cleared', (payload: unknown) => applyCleared(payload));

  return {
    memories: rows,
    lastMemorySyncMs,
    seedMemoriesFromInit: seedFromInit,
    clearShortTermMemory,
  };
}
