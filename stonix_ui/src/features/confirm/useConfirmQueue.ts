import { useCallback, useEffect, useRef, useState } from 'react';
import { normalizeConfirm, toEpochMs, type NormalizedConfirm } from '@/shared/contracts/events';
import { useSocketEvent } from '@/shared/realtime/subscriptions';

export interface QueuedConfirm extends NormalizedConfirm {
  receivedAt: number;
  deadlineMs: number;
}

export interface ConfirmDecision {
  id: string;
  allow: boolean;
  reason: string;
  ts: number;
  tool?: string;
  summary?: string;
  args_preview?: string;
  expired?: boolean;
}

function pickConfirmList(state: Record<string, unknown>): unknown[] {
  const keys = ['confirmations', 'pending_confirmations', 'pending_confirms'] as const;
  for (const key of keys) {
    const value = state[key];
    if (Array.isArray(value) && value.length > 0) return value;
  }
  for (const key of keys) {
    const value = state[key];
    if (Array.isArray(value)) return value;
  }
  return [];
}

function computeDeadline(
  item: NormalizedConfirm,
  receivedAt: number,
  serverOffsetMs: number,
): number {
  const ttlCap =
    item.ttl_ms != null && Number.isFinite(item.ttl_ms) ? receivedAt + item.ttl_ms : undefined;
  let deadline: number;
  if (item.expires_at != null && Number.isFinite(item.expires_at)) {
    deadline = item.expires_at - serverOffsetMs;
  } else if (ttlCap != null) {
    deadline = ttlCap;
  } else {
    deadline = receivedAt + 120_000;
  }
  if (ttlCap != null) deadline = Math.min(deadline, ttlCap);
  return deadline;
}

function toQueued(raw: unknown, receivedAt: number, serverOffsetMs: number): QueuedConfirm | null {
  const normalized = normalizeConfirm(raw);
  if (!normalized.id) return null;
  const deadlineMs = computeDeadline(normalized, receivedAt, serverOffsetMs);
  return { ...normalized, receivedAt, deadlineMs };
}

function sortOldestFirst(items: QueuedConfirm[]): QueuedConfirm[] {
  return [...items].sort((a, b) => a.receivedAt - b.receivedAt);
}

export function useConfirmQueue() {
  const [queue, setQueue] = useState<QueuedConfirm[]>([]);
  const [decisions, setDecisions] = useState<ConfirmDecision[]>([]);
  const [cancelNotice, setCancelNotice] = useState(false);
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  const respondedIds = useRef(new Set<string>());
  const cancelTimerRef = useRef<number | null>(null);
  const enqueueRef = useRef<(raw: unknown) => void>(() => undefined);
  const resolveRef = useRef<(raw: unknown) => void>(() => undefined);

  const current = queue[0] ?? null;

  const setServerTime = useCallback((serverTime: unknown) => {
    const serverMs = toEpochMs(serverTime);
    if (serverMs == null) return;
    setServerOffsetMs(serverMs - Date.now());
  }, []);

  const recordDecision = useCallback(
    (id: string, allow: boolean, reason: string, item?: QueuedConfirm, expired = false) => {
      const entry: ConfirmDecision = {
        id,
        allow,
        reason,
        ts: Date.now(),
        tool: item?.tool,
        summary: item?.summary,
        args_preview: item?.args_preview,
        expired,
      };
      setDecisions(prev => [entry, ...prev].slice(0, 20));
    },
    [],
  );

  const enqueue = useCallback(
    (raw: unknown) => {
      const receivedAt = Date.now();
      const item = toQueued(raw, receivedAt, serverOffsetMs);
      if (!item) return;
      setQueue(prev => {
        if (prev.some(row => row.id === item.id)) return prev;
        return sortOldestFirst([...prev, item]);
      });
    },
    [serverOffsetMs],
  );

  const seedFromInit = useCallback(
    (state: Record<string, unknown>) => {
      const list = pickConfirmList(state);
      if (!list.length) return;
      const receivedAt = Date.now();
      const seeded = list
        .map(raw => toQueued(raw, receivedAt, serverOffsetMs))
        .filter((row): row is QueuedConfirm => row != null);
      if (!seeded.length) return;
      setQueue(prev => {
        const byId = new Map<string, QueuedConfirm>();
        for (const row of prev) byId.set(row.id, row);
        for (const row of seeded) {
          if (!byId.has(row.id)) byId.set(row.id, row);
        }
        return sortOldestFirst([...byId.values()]);
      });
    },
    [serverOffsetMs],
  );

  const resolve = useCallback(
    (raw: unknown) => {
      const normalized = normalizeConfirm(raw);
      if (!normalized.id) return;
      respondedIds.current.delete(normalized.id);

      const reason = (normalized.reason ?? '').toLowerCase();
      const isCancelled = reason.includes('cancel') || reason === 'cancelled';

      if (isCancelled || reason === 'cancelled') {
        setCancelNotice(true);
        if (cancelTimerRef.current != null) window.clearTimeout(cancelTimerRef.current);
        cancelTimerRef.current = window.setTimeout(() => {
          setCancelNotice(false);
          cancelTimerRef.current = null;
        }, 1600);
      }

      setQueue(prev => {
        const item = prev.find(row => row.id === normalized.id);
        recordDecision(
          normalized.id,
          normalized.allow ?? false,
          normalized.reason ?? 'resolved',
          item,
        );
        return prev.filter(row => row.id !== normalized.id);
      });
    },
    [recordDecision],
  );

  const markResponded = useCallback((id: string): boolean => {
    if (respondedIds.current.has(id)) return false;
    respondedIds.current.add(id);
    return true;
  }, []);

  enqueueRef.current = enqueue;
  resolveRef.current = resolve;

  useSocketEvent('confirm_request', (payload: unknown) => enqueueRef.current(payload));
  useSocketEvent('confirm_resolved', (payload: unknown) => resolveRef.current(payload));

  const purgeExpired = useCallback(() => {
    const now = Date.now();
    setQueue(prev => {
      const expiredRows = prev.filter(item => item.deadlineMs <= now);
      for (const item of expiredRows) {
        recordDecision(item.id, false, 'expired', item, true);
      }
      const next = prev.filter(item => item.deadlineMs > now);
      return next.length === prev.length ? prev : next;
    });
  }, [recordDecision]);

  useEffect(() => {
    const timer = window.setInterval(purgeExpired, 400);
    return () => window.clearInterval(timer);
  }, [purgeExpired]);

  useEffect(
    () => () => {
      if (cancelTimerRef.current != null) window.clearTimeout(cancelTimerRef.current);
    },
    [],
  );

  return {
    queue,
    current,
    queueCount: queue.length,
    decisions,
    cancelNotice,
    setServerTime,
    seedFromInit,
    enqueue,
    resolve,
    markResponded,
    purgeExpired,
  };
}

export type ConfirmQueueState = ReturnType<typeof useConfirmQueue>;
