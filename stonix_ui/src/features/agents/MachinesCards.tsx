import { useEffect, useState } from 'react';
import { Cpu, HardDrive, MemoryStick } from 'lucide-react';
import { Card } from '@/shared/ui/Card';
import type { VitalsSnapshot } from '@/features/command-center/hooks/useCommandCenter';
import TelemetryCard, { type TelemetrySnapshot } from '@/features/telemetry/TelemetryCard';
import { formatRelativeAgo } from '@/shared/time';

const STALE_MS = 30_000;
const HIGH_THRESHOLD = 85;

function metricValue(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

function vitalsAgeMs(vitals: VitalsSnapshot, nowMs: number): number | null {
  const ts = vitals.updatedAtMs;
  if (typeof ts !== 'number' || !Number.isFinite(ts) || ts <= 0) return null;
  return Math.max(0, nowMs - ts);
}

function VitalsCard({
  testId,
  icon,
  title,
  subtitle,
  vitals,
  diskLabel,
  nowMs,
}: {
  testId: string;
  icon: 'laptop' | 'server';
  title: string;
  subtitle?: string;
  vitals: VitalsSnapshot;
  diskLabel: string;
  nowMs: number;
}) {
  const ageMs = vitalsAgeMs(vitals, nowMs);
  const stale = ageMs == null || ageMs > STALE_MS;
  const agoSec = ageMs == null ? Number.NaN : ageMs / 1000;

  return (
    <Card
      icon={icon}
      title={title}
      subtitle={subtitle}
      boundaryName={title}
      className="agents-card agents-card--vitals"
    >
      <div data-testid={testId} className="space-y-3">
        <Metric icon={Cpu} label="CPU" value={metricValue(vitals.cpu)} />
        <Metric icon={MemoryStick} label="Memory" value={metricValue(vitals.ram)} />
        <Metric icon={HardDrive} label={diskLabel} value={metricValue(vitals.disk)} />
        <div className="flex items-center justify-between gap-2 text-xs text-command-muted">
          <span data-testid={`${testId}-updated`}>updated {formatRelativeAgo(agoSec)}</span>
          {stale ? (
            <span
              className="rounded border border-warn-border-strong bg-warn-surface px-1.5 py-0.5 uppercase tracking-wide text-command-warning"
              data-testid={`${testId}-stale`}
            >
              stale
            </span>
          ) : null}
        </div>
      </div>
    </Card>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Cpu;
  label: string;
  value: number;
}) {
  const high = value >= HIGH_THRESHOLD;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-command-muted">
          <Icon size={13} />
          {label}
        </div>
        <span className="font-mono text-xs text-command-text">
          {high ? <span aria-hidden>▲ </span> : null}
          {value.toFixed(1)}%
        </span>
      </div>
      <div className="metric-track">
        <div className="metric-fill" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
      </div>
    </div>
  );
}

export type MachinesCardsProps = {
  vitalsPc: VitalsSnapshot;
  vitalsVps: VitalsSnapshot;
  telemetry: TelemetrySnapshot;
  brain: string | null;
};

export function MachinesCards({ vitalsPc, vitalsVps, telemetry, brain }: MachinesCardsProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const pcSubtitle = vitalsPc.host ? vitalsPc.host : undefined;
  const vpsSubtitle = 'Ubuntu VPS';

  return (
    <div className="agents-machines-grid" data-anchor="machines">
      <VitalsCard
        testId="vitals-pc"
        icon="laptop"
        title="PC (laptop)"
        subtitle={pcSubtitle}
        vitals={vitalsPc}
        diskLabel={vitalsPc.disk_label ? `Disk (${vitalsPc.disk_label})` : 'Disk (C:)'}
        nowMs={nowMs}
      />
      <VitalsCard
        testId="vitals-vps"
        icon="server"
        title="Agent (VPS)"
        subtitle={vpsSubtitle}
        vitals={vitalsVps}
        diskLabel={vitalsVps.disk_label ? `Disk (${vitalsVps.disk_label})` : 'Disk'}
        nowMs={nowMs}
      />
      <div data-testid="telemetry-card" className="agents-card-wrap agents-card-wrap--telemetry">
        <Card
          icon="chart"
          title="Telemetry"
          subtitle="Last 24 h of turns."
          boundaryName="Telemetry"
          className="agents-card agents-card--telemetry"
        >
          <TelemetryCard telemetry={telemetry} brain={brain} embedded />
        </Card>
      </div>
    </div>
  );
}

export default MachinesCards;
