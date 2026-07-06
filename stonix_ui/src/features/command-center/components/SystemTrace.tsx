import { TerminalSquare } from 'lucide-react';
import { ReasoningPlan, VortexLog } from '../hooks/useCommandCenter';
import { Panel, StatusDot } from '@/shared/ui/Panel';

interface SystemTraceProps {
  logs: VortexLog[];
  reasoning: ReasoningPlan | null;
  isConnected: boolean;
}

const SystemTrace = ({ logs, reasoning, isConnected }: SystemTraceProps) => (
  <Panel
    className="system-trace"
    title="System Logs"
    action={
      <div className="flex items-center gap-2 text-[10px] text-command-muted">
        <StatusDot active={isConnected} tone={isConnected ? 'accent' : 'danger'} />
        {isConnected ? 'connected' : 'offline'}
      </div>
    }
  >
    <div className="system-trace__grid">
      <div className="trace-terminal custom-scrollbar">
        {logs.length === 0 ? (
          <div className="trace-empty">
            <TerminalSquare size={18} />
            Waiting for bridge events
          </div>
        ) : (
          logs.slice(0, 18).map((log, index) => (
            <div key={`${log.timestamp}-${index}`} className="trace-line">
              <span className="trace-line__time">{formatTime(log.timestamp)}</span>
              <span className="trace-line__category">{log.category || 'system'}</span>
              <span className="trace-line__text">{log.text}</span>
            </div>
          ))
        )}
      </div>

      <div className="reasoning-card">
        <span className="panel-kicker">Reasoning</span>
        <p className="mt-2 text-sm font-medium text-command-text">
          {reasoning?.intent || 'No active reasoning plan'}
        </p>
        <div className="mt-4 space-y-2">
          {(reasoning?.plan || []).slice(0, 4).map((step, index) => (
            <div key={`${step}-${index}`} className="reasoning-step">
              <span>{index + 1}</span>
              <p>{step}</p>
            </div>
          ))}
          {!reasoning?.plan?.length && (
            <p className="text-xs leading-5 text-command-muted">
              Execution plans will appear here when the assistant needs tools or multi-step work.
            </p>
          )}
        </div>
      </div>
    </div>
  </Panel>
);

const formatTime = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });

export default SystemTrace;
