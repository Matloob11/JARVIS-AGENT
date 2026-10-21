import { useCallback, useRef, useState } from 'react';
import { toast } from '@/toast';
import { normalizeTasks, toEpochMs } from '@/shared/contracts/events';
import { useSocketEvent } from '@/shared/realtime/subscriptions';
import { sendNotificationAck } from '@/features/notifications/notificationAck';

export type TaskSource = 'task' | 'reminder';

export interface TaskItem {
  id: string;
  description: string;
  /** Epoch ms, or null when the bridge sent no due time ("koi waqt nahi"). */
  due_at: number | null;
  source: TaskSource;
  kind: string;
  status?: string;
}

export interface TaskNotification {
  id: string;
  description: string;
  due_at?: number;
  source?: TaskSource;
  kind?: string;
  spoken?: boolean;
  action?: string;
  ts: number;
}

const MAX_NOTIFICATIONS = 20;

function normalizeSource(raw: unknown): TaskSource {
  const s = String(raw ?? 'task').toLowerCase();
  return s === 'reminder' ? 'reminder' : 'task';
}

export function normalizeTaskItem(raw: unknown): TaskItem | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const id = data.id != null ? String(data.id).trim() : '';
  if (!id) return null;
  const description =
    String(data.description ?? data.text ?? '').trim() || '(no description)';
  const dueRaw = data.due_at ?? data.dueAt ?? data.scheduled_time;
  // null/missing → no due (never coerce to Date.now() / "Due now").
  const due_at =
    dueRaw == null || dueRaw === ''
      ? null
      : toEpochMs(dueRaw) ?? null;
  const source = normalizeSource(data.source ?? data.kind);
  const kind = String(data.kind ?? source).toLowerCase();
  const status = data.status != null ? String(data.status) : undefined;
  return { id, description, due_at, source, kind, status };
}

export function normalizeTaskNotification(raw: unknown): TaskNotification | null {
  if (!raw || typeof raw !== 'object') return null;
  const data = raw as Record<string, unknown>;
  const id = data.id != null ? String(data.id).trim() : '';
  if (!id) return null;
  const description =
    String(data.description ?? data.text ?? '').trim() || '(no description)';
  const due_at = toEpochMs(data.due_at ?? data.dueAt);
  const source = data.source != null ? normalizeSource(data.source) : undefined;
  const kind = data.kind != null ? String(data.kind) : undefined;
  const spoken = typeof data.spoken === 'boolean' ? data.spoken : undefined;
  const action = data.action != null ? String(data.action) : undefined;
  const ts = toEpochMs(data.ts ?? data.timestamp) ?? Date.now();
  return { id, description, due_at, source, kind, spoken, action, ts };
}

function sortByDueAsc(rows: TaskItem[]): TaskItem[] {
  return [...rows].sort((a, b) => {
    if (a.due_at == null && b.due_at == null) return 0;
    if (a.due_at == null) return 1;
    if (b.due_at == null) return -1;
    return a.due_at - b.due_at;
  });
}

function prependNotification(prev: TaskNotification[], next: TaskNotification): TaskNotification[] {
  const without = prev.filter(row => row.id !== next.id);
  return [next, ...without].slice(0, MAX_NOTIFICATIONS);
}

function formatDueLabel(dueMs?: number): string {
  if (dueMs == null) return '';
  return new Date(dueMs).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function showTaskToast(note: TaskNotification) {
  const dueLabel = formatDueLabel(note.due_at);
  const parts = [note.description];
  if (dueLabel) parts.push(`Due ${dueLabel}`);
  if (note.spoken === true) parts.push('Spoken aloud');
  if (note.spoken === false) parts.push('Screen only');
  toast(parts.join(' · '), {
    duration: 10_000,
    icon: note.spoken === false ? '📋' : '🔊',
    id: `task-toast-${note.id}-${note.ts}`,
  });
}

function maybeBrowserNotification(note: TaskNotification) {
  if (typeof Notification === 'undefined') return;
  if (Notification.permission !== 'granted') return;
  const due = note.due_at ? ` — due ${formatDueLabel(note.due_at)}` : '';
  try {
    new Notification('Task reminder', {
      body: `${note.description}${due}`,
      tag: `jarvis-task-${note.id}`,
    });
  } catch {
    /* ignore */
  }
}

export function useTasks(emitCommand: (type: string, payload?: unknown) => void) {
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [notifications, setNotifications] = useState<TaskNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  // C-125 B5: known / acked reminder ids must not re-toast or bump unread.
  const knownNotifIdsRef = useRef(new Set<string>());
  const seededPendingAckRef = useRef(new Set<string>());

  const applyTaskSync = useCallback((raw: unknown) => {
    const normalized = normalizeTasks(raw);
    const list = (normalized.tasks ?? [])
      .map(item => normalizeTaskItem(item))
      .filter((row): row is TaskItem => row != null);
    setTasks(sortByDueAsc(list));
  }, []);

  const applyNotification = useCallback((raw: unknown) => {
    const note = normalizeTaskNotification(raw);
    if (!note) return;
    const known = knownNotifIdsRef.current.has(note.id);
    if (known) {
      // Re-delivery of same id: ack again, no toast / browser / unread.
      sendNotificationAck(note.id);
      return;
    }
    knownNotifIdsRef.current.add(note.id);
    setNotifications(prev => prependNotification(prev, note));
    setUnreadCount(c => c + 1);
    showTaskToast(note);
    maybeBrowserNotification(note);
    sendNotificationAck(note.id);
  }, []);

  const seedFromInit = useCallback((state: Record<string, unknown>) => {
    if (Array.isArray(state.tasks)) {
      applyTaskSync({ tasks: state.tasks });
    }
    const seedNotes = state.notifications;
    if (!Array.isArray(seedNotes) || seedNotes.length === 0) return;
    const seeded = seedNotes
      .map(raw => normalizeTaskNotification(raw))
      .filter((row): row is TaskNotification => row != null)
      .slice(0, MAX_NOTIFICATIONS);
    if (seeded.length) {
      for (const row of seeded) {
        knownNotifIdsRef.current.add(row.id);
        seededPendingAckRef.current.add(row.id);
      }
      setNotifications(prev => {
        const byId = new Map<string, TaskNotification>();
        for (const row of [...seeded, ...prev]) {
          if (!byId.has(row.id)) byId.set(row.id, row);
        }
        return [...byId.values()].slice(0, MAX_NOTIFICATIONS);
      });
    }
  }, [applyTaskSync]);

  const cancelTask = useCallback(
    (id: string, source: TaskSource) => {
      emitCommand('task_cancel', { id, source });
    },
    [emitCommand],
  );

  const refreshTasks = useCallback(() => {
    emitCommand('tasks_refresh', {});
  }, [emitCommand]);

  const clearUnread = useCallback(() => {
    setUnreadCount(0);
  }, []);

  const openTaskBell = useCallback(() => {
    if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
      void Notification.requestPermission();
    }
    // C-125 B5: ack seeded init_state.notifications when the bell (toast surface) opens.
    for (const id of [...seededPendingAckRef.current]) {
      sendNotificationAck(id);
      seededPendingAckRef.current.delete(id);
    }
    clearUnread();
  }, [clearUnread]);

  useSocketEvent('task_sync', (payload: unknown) => applyTaskSync(payload));
  useSocketEvent('task_notification', (payload: unknown) => applyNotification(payload));
  useSocketEvent('reminder_due', (payload: unknown) => applyNotification(payload));

  return {
    tasks,
    taskNotifications: notifications,
    taskUnreadCount: unreadCount,
    seedTasksFromInit: seedFromInit,
    cancelTask,
    refreshTasks,
    clearTaskUnread: clearUnread,
    openTaskBell,
  };
}
