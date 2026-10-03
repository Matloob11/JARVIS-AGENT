import { useCallback, useMemo, useState, type KeyboardEvent } from 'react';
import { Pause, Play, TerminalSquare, Trash2 } from 'lucide-react';
import clsx from 'clsx';
import type { NormalizedPlan } from '@/shared/contracts/events';
import {
  type PlanProgressLine,
  type ReasoningPlan,
  type SystemFeedEntry,
  type VortexLog,
} from '../hooks/useCommandCenter';
import { Panel, StatusDot } from '@/shared/ui/Panel';
import { ErrorBoundary } from '@/shared/ui/ErrorBoundary';

interface SystemTraceProps {
  logs: VortexLog[];
  reasoning: ReasoningPlan | null;
  reasoningHistory: ReasoningPlan[];
  planProgress: PlanProgressLine | null;
  systemFeed: SystemFeedEntry[];
  logDroppedCount: number;
  traceLogBufferCount: number;
  isConnected: boolean;
  onLogsPausedChange: (paused: boolean) => void;
}

type TraceTab = 'logs' | 'reasoning';

const E2E_MALFORMED_PROBE = '__MALFORMED_PROBE__';

const SystemTrace = ({
  logs,
  reasoning,
  reasoningHistory,
  planProgress,
  systemFeed,
  logDroppedCount,
  traceLogBufferCount,
  isConnected,
  onLogsPausedChange,
}: SystemTraceProps) => {
  const [traceTab, setTraceTab] = useState<TraceTab>('logs');
  const [showInfo, setShowInfo] = useState(false);
  const [filterText, setFilterText] = useState('');
  const [paused, setPaused] = useState(false);
  const [hiddenLogCount, setHiddenLogCount] = useState(0);
  const [selectedReasoningIdx, setSelectedReasoningIdx] = useState(0);

  const history = reasoningHistory.length ? reasoningHistory : reasoning ? [reasoning] : [];
  const selectedReasoning = history[selectedReasoningIdx] ?? history[0] ?? null;

  const visibleLogs = useMemo(() => {
    const trimmedFilter = filterText.trim().toLowerCase();
    return logs
      .slice(0, Math.max(0, logs.length - hiddenLogCount))
      .filter(log => {
        const level = (log.level ?? 'INFO').toUpperCase();
        if (!showInfo && (level === 'INFO' || level === 'DEBUG')) return false;
        if (!trimmedFilter) return true;
        const haystack = `${log.category} ${log.text} ${level}`.toLowerCase();
        return haystack.includes(trimmedFilter);
      });
  }, [filterText, hiddenLogCount, logs, showInfo]);

  const togglePause = () => {
    const next = !paused;
    setPaused(next);
    onLogsPausedChange(next);
  };

  const clearView = () => {
    setHiddenLogCount(logs.length);
  };

  const selectTraceTab = useCallback((tab: TraceTab) => {
    setTraceTab(tab);
  }, []);

  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    selectTraceTab(traceTab === 'logs' ? 'reasoning' : 'logs');
  };

  const pauseLabel = paused ? 'Resume' : 'Pause';
  const pauseCount =
    paused && traceLogBufferCount > 0 ? ` (${traceLogBufferCount})` : '';

  return (
    <Panel
      className="system-trace"
      title="System Logs"
      action={
        <div className="system-trace__live">
          <StatusDot active={isConnected} tone={isConnected ? 'accent' : 'danger'} />
          <span>{isConnected ? 'Live' : 'Offline'}</span>
        </div>
      }
    >
      <div
        className="system-trace__tabs"
        data-testid="system-trace-tabs"
        role="tablist"
        aria-label="System logs views"
        onKeyDown={onTabKeyDown}
      >
        <button
          type="button"
          role="tab"
          id="trace-tab-logs-btn"
          aria-selected={traceTab === 'logs'}
          aria-controls="trace-panel-logs"
          tabIndex={traceTab === 'logs' ? 0 : -1}
          className={clsx('system-trace__tab', traceTab === 'logs' && 'is-active')}
          data-testid="trace-tab-logs"
          onClick={() => selectTraceTab('logs')}
        >
          Logs
        </button>
        <button
          type="button"
          role="tab"
          id="trace-tab-reasoning-btn"
          aria-selected={traceTab === 'reasoning'}
          aria-controls="trace-panel-reasoning"
          tabIndex={traceTab === 'reasoning' ? 0 : -1}
          className={clsx('system-trace__tab', traceTab === 'reasoning' && 'is-active')}
          data-testid="trace-tab-reasoning"
          onClick={() => selectTraceTab('reasoning')}
        >
          Reasoning
        </button>
      </div>

      <div className="system-trace__grid" data-testid="system-trace-panel">
        <div
          id="trace-panel-logs"
          role="tabpanel"
          aria-labelledby="trace-tab-logs-btn"
          className={clsx('trace-terminal-wrap', traceTab === 'reasoning' && 'is-hidden-narrow')}
        >
          <div className="trace-toolbar">
            <div className="trace-level-chips">
              <span className="trace-chip trace-chip--active">WARN+</span>
              <button
                type="button"
                className={clsx('trace-chip trace-chip--toggle', showInfo && 'is-active')}
                data-testid="trace-info-toggle"
                onClick={() => setShowInfo(value => !value)}
              >
                INFO
              </button>
            </div>
            <input
              className="trace-filter"
              placeholder="Filter logs…"
              aria-label="Filter logs"
              value={filterText}
              onChange={event => setFilterText(event.target.value)}
              data-testid="trace-log-filter"
            />
            <button
              type="button"
              className="trace-toolbar__btn"
              aria-pressed={paused}
              data-testid="trace-log-pause"
              onClick={togglePause}
            >
              {paused ? <Play size={12} /> : <Pause size={12} />}
              {pauseLabel}
              {pauseCount}
            </button>
            <button
              type="button"
              className="trace-toolbar__btn"
              data-testid="trace-log-clear"
              onClick={clearView}
            >
              <Trash2 size={12} />
              Clear
            </button>
          </div>
          {logDroppedCount > 0 && (
            <p className="trace-dropped" data-testid="trace-log-dropped">
              <span aria-hidden>▲ </span>
              Bridge ne {logDroppedCount}{' '}
              <span className="trace-dropped__word">purane logs</span> drop kiye
            </p>
          )}
          <div
            className="trace-terminal custom-scrollbar"
            data-testid="trace-log-terminal"
            role="log"
            tabIndex={0}
            aria-label="System log stream"
          >
            {visibleLogs.length === 0 ? (
              <div className="trace-empty">
                <TerminalSquare size={18} />
                Waiting for bridge events
              </div>
            ) : (
              visibleLogs.map((log, index) => (
                <div
                  key={`${log.timestamp}-${log.category}-${index}`}
                  className="trace-line"
                  data-testid="trace-log-line"
                  data-level={(log.level ?? 'INFO').toUpperCase()}
                >
                  <span className="trace-line__time">{formatTime(log.timestamp)}</span>
                  <span className="trace-line__level">{(log.level ?? 'INFO').toUpperCase()}</span>
                  <span className="trace-line__category">{log.category || 'system'}</span>
                  <span className="trace-line__text">
                    {log.text}
                    {(log.repeat ?? 1) > 1 ? ` ×${log.repeat}` : ''}
                  </span>
                </div>
              ))
            )}
          </div>
          {systemFeed.length > 0 && (
            <div className="trace-system-feed" data-testid="trace-system-feed">
              <span className="panel-kicker">System</span>
              <ul>
                {systemFeed.slice(0, 6).map((entry, index) => (
                  <li key={`${entry.kind}-${entry.ts}-${index}`}>
                    <span className="trace-system-feed__kind">{entry.kind}</span>
                    <span className="trace-system-feed__text">{entry.summary}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <ErrorBoundary name="SystemTraceReasoning">
          <ReasoningCard
            reasoning={selectedReasoning}
            history={history}
            selectedIndex={selectedReasoningIdx}
            onSelectIndex={setSelectedReasoningIdx}
            planProgress={planProgress}
            className={clsx(traceTab === 'logs' && 'is-hidden-narrow')}
            tabId="trace-panel-reasoning"
          />
        </ErrorBoundary>
      </div>
    </Panel>
  );
};

interface ReasoningCardProps {
  reasoning: ReasoningPlan | null;
  history: ReasoningPlan[];
  selectedIndex: number;
  onSelectIndex: (index: number) => void;
  planProgress: PlanProgressLine | null;
  className?: string;
  tabId: string;
}

const ReasoningCard = ({
  reasoning,
  history,
  selectedIndex,
  onSelectIndex,
  planProgress,
  className,
  tabId,
}: ReasoningCardProps) => {
  if (reasoning?.intent === E2E_MALFORMED_PROBE) {
    throw new Error('reasoning probe');
  }

  const planLines = (reasoning?.plan ?? [])
    .filter((line: string) => line.trim().length > 0)
    .slice(0, 6);
  const confidenceEntries = topConfidenceEntries(reasoning);

  return (
    <div
      id={tabId}
      role="tabpanel"
      aria-labelledby="trace-tab-reasoning-btn"
      className={clsx('reasoning-card', className)}
      data-testid="trace-reasoning-card"
      aria-label="Reasoning"
    >
      <div className="reasoning-card__header">
        <span className="panel-kicker">Reasoning</span>
        {history.length > 1 && (
          <select
            className="reasoning-card__select"
            value={selectedIndex}
            onChange={event => onSelectIndex(Number(event.target.value))}
            data-testid="trace-reasoning-select"
            aria-label="Reasoning history"
            title={formatReasoningOption(history[selectedIndex] ?? history[0], selectedIndex)}
          >
            {history.slice(0, 5).map((item, index) => (
              <option key={`${item.msg_id ?? item.intent ?? index}-${index}`} value={index}>
                {formatReasoningOption(item, index)}
              </option>
            ))}
          </select>
        )}
      </div>
      <div className="reasoning-card__title-row">
        <p className="text-sm font-medium text-command-text">
          {reasoning?.intent || 'No active reasoning plan'}
        </p>
        {reasoning?.is_ambiguous && (
          <span className="reasoning-badge reasoning-badge--warn" data-testid="trace-ambiguity-badge">
            ambiguous
          </span>
        )}
        {reasoning?.source && (
          <span className="reasoning-badge reasoning-badge--source">{reasoning.source}</span>
        )}
      </div>
      {reasoning?.ts != null && (
        <p className="reasoning-card__time">{formatReasoningTime(reasoning.ts)}</p>
      )}
      {confidenceEntries.length > 0 && (
        <div className="reasoning-confidence" data-testid="trace-confidence-bars">
          {confidenceEntries.map(entry => (
            <div key={entry.label} className="reasoning-confidence__row">
              <span>{entry.label}</span>
              <div className="reasoning-confidence__track">
                <div
                  className="reasoning-confidence__fill"
                  style={{ width: `${Math.round(entry.value * 100)}%` }}
                />
              </div>
              <span>{Math.round(entry.value * 100)}%</span>
            </div>
          ))}
        </div>
      )}
      {planProgress && (
        <p className="reasoning-plan-progress" data-testid="trace-plan-progress">
          Step {planProgress.current}/{planProgress.total} - {planProgress.action} ({planProgress.status})
        </p>
      )}
      <div className="mt-3 space-y-2">
        {planLines.map((step: string, index: number) => (
          <div key={`${step}-${index}`} className="reasoning-step" data-testid="trace-reasoning-step">
            <span>{index + 1}</span>
            <p>{step}</p>
          </div>
        ))}
        {!planLines.length && (
          <p className="text-xs leading-5 text-command-muted">
            Execution plans will appear here when the assistant needs tools or multi-step work.
          </p>
        )}
      </div>
    </div>
  );
};

function topConfidenceEntries(reasoning: NormalizedPlan | null): { label: string; value: number }[] {
  if (!reasoning?.confidence) return [];
  const raw = reasoning.confidence;
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return [{ label: 'intent', value: clamp01(raw) }];
  }
  if (typeof raw !== 'object' || raw === null) return [];
  return Object.entries(raw as Record<string, unknown>)
    .map(([label, value]) =>
      typeof value === 'number' && Number.isFinite(value) ? { label, value: clamp01(value) } : null,
    )
    .filter((entry): entry is { label: string; value: number } => entry != null)
    .sort((a, b) => b.value - a.value)
    .slice(0, 3);
}

function clamp01(value: number): number {
  if (value > 1 && value <= 100) return value / 100;
  return Math.min(1, Math.max(0, value));
}

function formatReasoningOption(item: ReasoningPlan, index: number): string {
  const label = item.intent?.trim() || `Plan ${index + 1}`;
  const time = item.ts != null ? formatReasoningTime(item.ts) : '';
  return time ? `${label} · ${time}` : label;
}

const formatTime = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });

const formatReasoningTime = (tsMs: number) =>
  new Date(tsMs).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

export default SystemTrace;
