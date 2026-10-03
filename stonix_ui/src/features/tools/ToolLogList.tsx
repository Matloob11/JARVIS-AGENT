import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Loader2 } from 'lucide-react';
import type { ToolLogEntry, ToolLogStatus } from './useToolLogs';

type StatusFilter = 'all' | ToolLogStatus | 'issues';

interface ToolLogListProps {
  rows: ToolLogEntry[];
}

const ISSUE_STATUSES: ToolLogStatus[] = [
  'error',
  'blocked',
  'denied',
  'timeout',
  'cancelled',
  'needs_confirmation',
  'degraded',
];

function relativeTime(tsMs: number): string {
  const sec = Math.max(0, Math.floor((Date.now() - tsMs) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  return `${hr}h ago`;
}

function statusLabel(status: ToolLogStatus): string {
  return status.replace(/_/g, ' ');
}

function pillClass(status: ToolLogStatus): string {
  if (status === 'success') return 'tool-pill tool-pill--success';
  if (status === 'error') return 'tool-pill tool-pill--error';
  if (status === 'running') return 'tool-pill tool-pill--running';
  return 'tool-pill tool-pill--warn';
}

function formatDuration(ms?: number): string {
  if (ms == null || !Number.isFinite(ms)) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const ToolLogList = ({ rows }: ToolLogListProps) => {
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const filtered = useMemo(() => {
    if (filter === 'all') return rows;
    if (filter === 'issues') return rows.filter(row => ISSUE_STATUSES.includes(row.status));
    return rows.filter(row => row.status === filter);
  }, [filter, rows]);

  if (rows.length === 0) {
    return (
      <div className="tool-panel-empty" data-testid="tool-log-empty">
        <p className="text-sm font-medium text-command-text">No tool actions yet</p>
        <p className="mt-2 text-xs text-command-muted">Waiting for backend bridge events.</p>
      </div>
    );
  }

  return (
    <div className="tool-log-list" data-testid="tool-log-list">
      <div className="tool-log-list__filters">
        <label className="text-xs font-semibold uppercase tracking-wide text-command-muted">
          Filter
          <select
            className="tool-log-filter"
            data-testid="tool-log-filter"
            aria-label="Filter logs"
            value={filter}
            onChange={event => setFilter(event.target.value as StatusFilter)}
          >
          <option value="all">All</option>
          <option value="running">Running</option>
          <option value="success">Success</option>
          <option value="error">Error</option>
          <option value="issues">Issues</option>
        </select>
        </label>
      </div>

      <div className="space-y-2">
        {filtered.map(row => {
          const isOpen = expanded[row.id] === true;
          const toggle = () =>
            setExpanded(prev => ({ ...prev, [row.id]: !isOpen }));
          return (
            <div
              key={row.id}
              className="tool-row"
              data-testid="tool-row"
              data-status={row.status}
              data-id={row.id}
            >
              <button type="button" className="tool-row__head" onClick={toggle}>
                <span className="tool-row__chevron">
                  {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </span>
                <span className="tool-row__name">{row.tool}</span>
                <span className={pillClass(row.status)}>
                  {row.status === 'running' && (
                    <Loader2 size={11} className="mr-1 inline animate-spin" />
                  )}
                  {statusLabel(row.status)}
                </span>
                <span className="tool-row__meta">{relativeTime(row.ts)}</span>
              </button>
              <div className="tool-row__summary">
                {row.progress != null && (
                  <span className="tool-row__progress">{row.progress}%</span>
                )}
                <span className="tool-row__duration">{formatDuration(row.duration_ms)}</span>
              </div>
              {row.details && !isOpen && (
                <p className="tool-row__preview">{row.details}</p>
              )}
              {isOpen && (
                <div className="tool-row__body">
                  {row.details && (
                    <div>
                      <p className="tool-row__label">Args / details</p>
                      <p className="tool-row__text">{row.details}</p>
                    </div>
                  )}
                  {row.result_summary && (
                    <div>
                      <p className="tool-row__label">Result</p>
                      <p className="tool-row__text">{row.result_summary}</p>
                    </div>
                  )}
                  {row.action && (
                    <p className="tool-row__text text-command-muted">Action: {row.action}</p>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ToolLogList;
