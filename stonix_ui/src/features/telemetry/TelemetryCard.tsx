import { Activity } from 'lucide-react';
import { StatusDot } from '@/shared/ui/Panel';

export interface TelemetrySnapshot {
  success_rate: number;
  avg_latency: number;
  confusion_rate?: number;
  total_samples?: number;
  status: string;
  source?: string;
}

function finiteOr(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function latencySeconds(avgLatency: number): number {
  if (!Number.isFinite(avgLatency)) return 0;
  return avgLatency > 20 ? avgLatency / 1000 : avgLatency;
}

function sourceLabel(source?: string, brain?: string | null): 'Agent (PC)' | 'Agent (VPS)' | 'Bridge' {
  const b = (brain ?? '').toLowerCase();
  if (b === 'vps') return 'Agent (VPS)';
  const normalized = (source ?? '').toLowerCase();
  if (normalized === 'agent' || normalized === 'pc' || normalized.includes('agent')) {
    return 'Agent (PC)';
  }
  return 'Bridge';
}

function statusTone(status: string): 'accent' | 'blue' | 'danger' {
  const s = status.toLowerCase();
  if (s.includes('critical') || s.includes('error') || s.includes('down')) return 'danger';
  if (s.includes('degrad') || s.includes('warn') || s.includes('unstable')) return 'blue';
  return 'accent';
}

const TelemetryCard = ({
  telemetry,
  brain,
  embedded = false,
}: {
  telemetry: TelemetrySnapshot;
  brain?: string | null;
  embedded?: boolean;
}) => {
  const successPct = finiteOr(telemetry.success_rate, 0) * (telemetry.success_rate <= 1 ? 100 : 1);
  const latencyS = latencySeconds(finiteOr(telemetry.avg_latency, 0));
  const confusionPct =
    finiteOr(telemetry.confusion_rate, 0) * (telemetry.confusion_rate != null && telemetry.confusion_rate <= 1 ? 100 : 1);
  const samples = Math.max(0, Math.floor(finiteOr(telemetry.total_samples, 0)));
  const tone = statusTone(telemetry.status || 'ok');

  return (
    <div
      className={embedded ? 'telemetry-card telemetry-card--embedded' : 'telemetry-card border-t border-white/5 pt-4'}
      data-testid={embedded ? undefined : 'telemetry-card'}
    >
      {embedded ? (
        <div className="mb-3 flex justify-end">
          <span className="text-xs uppercase tracking-wide text-command-muted" data-testid="telemetry-source">
            {sourceLabel(telemetry.source, brain)}
          </span>
        </div>
      ) : (
        <div className="mb-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-command-text">
            <Activity size={13} className="text-command-muted" />
            Telemetry
          </div>
          <span className="text-xs uppercase tracking-wide text-command-muted" data-testid="telemetry-source">
            {sourceLabel(telemetry.source, brain)}
          </span>
        </div>
      )}
      <div className="grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        <div>
          <p className="text-command-muted">Success</p>
          <p className="font-mono text-command-text" data-testid="telemetry-success">
            {successPct.toFixed(1)}%
          </p>
        </div>
        <div>
          <p className="text-command-muted">Avg latency</p>
          <p className="font-mono text-command-text" data-testid="telemetry-latency">
            {latencyS.toFixed(2)}s
          </p>
        </div>
        <div>
          <p className="text-command-muted">Confusion</p>
          <p className="font-mono text-command-text" data-testid="telemetry-confusion">
            {confusionPct.toFixed(1)}%
          </p>
        </div>
        <div>
          <p className="text-command-muted">Samples</p>
          <p className="font-mono text-command-text" data-testid="telemetry-samples">
            {samples.toLocaleString()}
          </p>
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2 text-xs text-command-muted">
        <StatusDot active tone={tone} />
        <span data-testid="telemetry-status">{telemetry.status || 'unknown'}</span>
      </div>
    </div>
  );
};

export default TelemetryCard;
