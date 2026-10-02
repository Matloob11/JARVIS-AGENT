import { useEffect, useMemo, useState } from 'react';
import type { TaskItem, TaskNotification, TaskSource } from './useTasks';

interface TasksPanelProps {
  rows: TaskItem[];
  notifications?: TaskNotification[];
  onCancelConfirmed: (id: string, source: TaskSource) => void;
  onRefresh: () => void;
}

function formatCountdown(dueMs: number, nowMs: number): string {
  const diffSec = Math.floor((dueMs - nowMs) / 1000);
  if (diffSec <= 0) {
    const overdue = Math.abs(diffSec);
    if (overdue < 60) return 'Due now';
    const min = Math.floor(overdue / 60);
    if (min < 60) return `Overdue ${min}m`;
    const hr = Math.floor(min / 60);
    return `Overdue ${hr}h ${min % 60}m`;
  }
  if (diffSec < 60) return `In ${diffSec}s`;
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `In ${min}m ${diffSec % 60}s`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `In ${hr}h ${min % 60}m`;
  const day = Math.floor(hr / 24);
  return `In ${day}d ${hr % 24}h`;
}

function formatDueClock(dueMs: number): string {
  return new Date(dueMs).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function kindPill(kind: string, source: TaskSource): string {
  if (source === 'reminder' || kind === 'reminder') return 'task-pill task-pill--reminder';
  return 'task-pill task-pill--task';
}

function statusPill(status?: string): string {
  const s = String(status ?? 'pending').toLowerCase();
  if (s === 'cancelled' || s === 'canceled') return 'task-pill task-pill--cancelled';
  if (s === 'completed' || s === 'done') return 'task-pill task-pill--done';
  if (s === 'failed') return 'task-pill task-pill--failed';
  if (s === 'running') return 'task-pill task-pill--running';
  return 'task-pill task-pill--pending';
}

function formatNotifTime(ts: number): string {
  return new Date(ts).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
}

const TasksPanel = ({
  rows,
  notifications = [],
  onCancelConfirmed,
  onRefresh,
}: TasksPanelProps) => {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [pendingCancel, setPendingCancel] = useState<{ id: string; source: TaskSource } | null>(
    null,
  );

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const sorted = useMemo(() => [...rows].sort((a, b) => a.due_at - b.due_at), [rows]);

  const requestCancel = (id: string, source: TaskSource) => {
    setPendingCancel({ id, source });
  };

  const confirmCancel = () => {
    if (!pendingCancel) return;
    onCancelConfirmed(pendingCancel.id, pendingCancel.source);
    setPendingCancel(null);
  };

  return (
    <div className="tasks-panel" data-testid="tasks-tab-panel">
      <div className="tasks-panel__toolbar">
        <button
          type="button"
          className="tasks-panel__refresh"
          data-testid="tasks-refresh-btn"
          onClick={() => onRefresh()}
        >
          Refresh
        </button>
      </div>

      {pendingCancel && (
        <div className="tasks-cancel-confirm" data-testid="task-cancel-confirm-panel">
          <p className="text-xs text-command-text">
            Cancel {pendingCancel.source} <span className="font-mono">{pendingCancel.id}</span>?
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              className="tasks-panel__btn tasks-panel__btn--danger"
              data-testid="task-cancel-confirm"
              onClick={confirmCancel}
            >
              Yes, cancel
            </button>
            <button
              type="button"
              className="tasks-panel__btn"
              data-testid="task-cancel-dismiss"
              onClick={() => setPendingCancel(null)}
            >
              Keep
            </button>
          </div>
        </div>
      )}

      {sorted.length === 0 ? (
        <div className="tool-panel-empty" data-testid="tasks-empty">
          <p className="text-sm font-medium text-command-text">No tasks or reminders</p>
          <p className="mt-2 text-xs text-command-muted">Waiting for backend bridge events.</p>
        </div>
      ) : (
        <div className="space-y-2" data-testid="tasks-list">
          {sorted.map(row => {
            const cancellable =
              !row.status ||
              ['pending', 'scheduled', 'queued'].includes(String(row.status).toLowerCase());
            return (
              <div
                key={row.id}
                className="task-row"
                data-testid="task-row"
                data-id={row.id}
                data-source={row.source}
              >
                <div className="task-row__head">
                  <span className={kindPill(row.kind, row.source)}>{row.kind || row.source}</span>
                  {row.status ? (
                    <span className={statusPill(row.status)}>{row.status}</span>
                  ) : null}
                  <span className="task-row__countdown" data-testid="task-countdown">
                    {formatCountdown(row.due_at, nowMs)}
                  </span>
                  <span className="task-row__due">{formatDueClock(row.due_at)}</span>
                </div>
                <p className="task-row__description">{row.description}</p>
                {cancellable && (
                  <button
                    type="button"
                    className="task-row__cancel"
                    data-testid="task-cancel-btn"
                    onClick={() => requestCancel(row.id, row.source)}
                  >
                    Cancel
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      <section className="tasks-reminder-history">
        <h4 className="tasks-reminder-history__title">
          Reminder history <small>Delivered notifications</small>
        </h4>
        {notifications.length === 0 ? (
          <p className="tasks-reminder-history__empty">No reminders delivered yet.</p>
        ) : (
          <div className="tasks-reminder-history__list">
            {notifications.map(note => (
              <div
                key={`${note.id}-${note.ts}`}
                className="tasks-reminder-history__row"
                data-testid="reminder-history-row"
              >
                <p>{note.description}</p>
                <time>{formatNotifTime(note.ts)}</time>
                <div className="tasks-reminder-history__tags">
                  {note.spoken === true ? (
                    <span className="task-pill task-pill--reminder">Spoken aloud</span>
                  ) : (
                    <span className="task-pill task-pill--muted">Screen only</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
};

export default TasksPanel;
