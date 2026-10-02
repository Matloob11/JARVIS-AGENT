import SystemTrace from '@/features/command-center/components/SystemTrace';
import type {
  PlanProgressLine,
  ReasoningPlan,
  SystemFeedEntry,
  VortexLog,
} from '@/features/command-center/hooks/useCommandCenter';

export type LogsTabProps = {
  logs: VortexLog[];
  reasoning: ReasoningPlan | null;
  reasoningHistory: ReasoningPlan[];
  planProgress: PlanProgressLine | null;
  systemFeed: SystemFeedEntry[];
  logDroppedCount: number;
  traceLogBufferCount: number;
  isConnected: boolean;
  onLogsPausedChange: (paused: boolean) => void;
};

const formatLogTime = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

function AllLogsList({ logs }: { logs: VortexLog[] }) {
  if (logs.length === 0) {
    return (
      <div className="all-logs-list all-logs-list--empty" data-testid="all-logs-list">
        <p className="all-logs-list__title">No system logs yet</p>
        <p className="all-logs-list__hint">Waiting for backend bridge events.</p>
      </div>
    );
  }

  return (
    <div className="all-logs-list custom-scrollbar" data-testid="all-logs-list">
      {logs.map((log, index) => (
        <article
          key={`${log.category}-${log.timestamp}-${index}`}
          className="all-logs-list__row"
          data-level={(log.level ?? 'INFO').toUpperCase()}
        >
          <div className="all-logs-list__meta">
            <span className="all-logs-list__level">{(log.level ?? 'INFO').toUpperCase()}</span>
            <span className="all-logs-list__category">{log.category || 'system'}</span>
            <time className="all-logs-list__time">{formatLogTime(log.timestamp)}</time>
          </div>
          <p className="all-logs-list__text">
            {log.text}
            {(log.repeat ?? 1) > 1 ? ` ×${log.repeat}` : ''}
          </p>
        </article>
      ))}
    </div>
  );
}

export function LogsTab({
  logs,
  reasoning,
  reasoningHistory,
  planProgress,
  systemFeed,
  logDroppedCount,
  traceLogBufferCount,
  isConnected,
  onLogsPausedChange,
}: LogsTabProps) {
  return (
    <div className="logs-tab">
      <div className="layout-zone layout-zone--trace activity-logs-trace" data-testid="layout-zone-trace">
        <SystemTrace
          logs={logs}
          reasoning={reasoning}
          reasoningHistory={reasoningHistory}
          planProgress={planProgress}
          systemFeed={systemFeed}
          logDroppedCount={logDroppedCount}
          traceLogBufferCount={traceLogBufferCount}
          isConnected={isConnected}
          onLogsPausedChange={onLogsPausedChange}
        />
      </div>
      <section className="all-logs-pane" aria-labelledby="all-logs-heading">
        <h3 id="all-logs-heading" className="all-logs-pane__title">
          All levels
        </h3>
        <AllLogsList logs={logs} />
      </section>
    </div>
  );
}

export default LogsTab;
