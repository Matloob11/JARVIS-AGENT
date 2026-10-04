import { useEffect, useMemo, useState } from 'react';
import type { VitalsSnapshot } from '@/features/command-center/hooks/useCommandCenter';
import { formatDiskCaption } from '@/features/overview/diskLabel';
import { Bar } from '@/shared/ui/Bar';
import { Icon } from '@/shared/ui/icons/Icon';

const STALE_MS = 30_000;

type QuickStatsCardProps = {
  vitalsPc: VitalsSnapshot;
  vitalsVps: VitalsSnapshot;
  laptopOnline: boolean;
  deviceCount: number;
  brain?: string | null;
  agentOnline?: boolean;
};

function metricPercent(value: number): number {
  return Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0));
}

function formatPercent(value: number): string {
  const rounded = Math.round(metricPercent(value));
  return `${rounded}%`;
}

function vitalsAgeMs(vitals: VitalsSnapshot, nowMs: number): number {
  const ts = vitals.updatedAtMs;
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return Number.POSITIVE_INFINITY;
  return Math.max(0, nowMs - ts);
}

const QuickStatsCard = ({
  vitalsPc,
  vitalsVps,
  laptopOnline,
  deviceCount,
  brain,
  agentOnline = false,
}: QuickStatsCardProps) => {
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const pcFresh = vitalsAgeMs(vitalsPc, nowMs) <= STALE_MS;
  const usePc =
    brain === 'pc' ? pcFresh && vitalsPc.updatedAtMs > 0 : laptopOnline;
  const source = usePc ? vitalsPc : vitalsVps;
  const stale = vitalsAgeMs(source, nowMs) > STALE_MS;

  const diskLabel = useMemo(() => {
    if (!usePc) return 'Disk (VPS)';
    return formatDiskCaption(source.disk_label, 'C:');
  }, [usePc, source.disk_label]);

  const subtitle =
    brain === 'pc'
      ? usePc
        ? 'Live system status'
        : agentOnline
          ? 'Laptop vitals purane: showing VPS'
          : 'Agent offline: showing VPS'
      : laptopOnline
        ? 'Live system status'
        : 'Laptop offline: showing Agent (VPS)';

  const cpuWarn = metricPercent(source.cpu) >= 85;
  const memWarn = metricPercent(source.ram) >= 90;
  const diskWarn = metricPercent(source.disk) >= 90;
  const deviceFill = Math.min(1, deviceCount / 11);

  return (
    <section
      className="overview-qs-card n-card edge"
      data-testid="qs-card"
    >
      <header className="n-hd">
        <Icon name="chart" className="overview-qs-card__icon" />
        <div className="min-w-0">
          <h3>Quick Stats</h3>
          <p>{subtitle}</p>
        </div>
        <div className="n-hd-act">
          <a
            className="n-btn xs icon overview-qs-card__open"
            href="#/agents/machines"
            aria-label="Open machines"
            data-testid="qs-open"
          >
            <Icon name="chev-r" />
          </a>
        </div>
      </header>

      {stale ? (
        <span className="overview-qs-card__stale" data-testid="qs-stale">
          stale
        </span>
      ) : null}

      <div className="overview-qs-card__tiles">
        <a
          className="overview-qs-tile n-tile edge"
          href="#/agents/machines"
          data-testid="qs-cpu"
        >
          <Icon name="cpu" className="overview-qs-tile__icon overview-qs-tile__icon--cpu" />
          <div className="overview-qs-tile__val">{formatPercent(source.cpu)}</div>
          <div className="overview-qs-tile__lab">
            <span className="overview-qs-tile__lab-long">CPU Usage</span>
            <span className="overview-qs-tile__lab-short">CPU</span>
          </div>
          {cpuWarn ? <span className="overview-qs-tile__warn" aria-hidden>▲</span> : null}
          <Bar value={metricPercent(source.cpu) / 100} className="overview-qs-tile__bar overview-qs-tile__bar--cpu" />
        </a>

        <a
          className="overview-qs-tile n-tile edge"
          href="#/agents/machines"
          data-testid="qs-memory"
        >
          <Icon name="memory" className="overview-qs-tile__icon overview-qs-tile__icon--mem" />
          <div className="overview-qs-tile__val">{formatPercent(source.ram)}</div>
          <div className="overview-qs-tile__lab">Memory</div>
          {memWarn ? <span className="overview-qs-tile__warn" aria-hidden>▲</span> : null}
          <Bar value={metricPercent(source.ram) / 100} className="overview-qs-tile__bar overview-qs-tile__bar--mem" />
        </a>

        <a
          className="overview-qs-tile n-tile edge"
          href="#/agents/machines"
          data-testid="qs-disk"
        >
          <Icon name="disk" className="overview-qs-tile__icon overview-qs-tile__icon--disk" />
          <div className="overview-qs-tile__val">{formatPercent(source.disk)}</div>
          <div className="overview-qs-tile__lab">{diskLabel}</div>
          {diskWarn ? <span className="overview-qs-tile__warn" aria-hidden>▲</span> : null}
          <Bar value={metricPercent(source.disk) / 100} className="overview-qs-tile__bar overview-qs-tile__bar--disk" />
        </a>

        <a
          className="overview-qs-tile n-tile edge"
          href="#/agents/laptop"
          data-testid="qs-devices"
        >
          <Icon name="laptop" className="overview-qs-tile__icon overview-qs-tile__icon--dev" />
          <div className="overview-qs-tile__val">{deviceCount}</div>
          <div className="overview-qs-tile__lab">
            <span className="overview-qs-tile__lab-long">Connected Devices</span>
            <span className="overview-qs-tile__lab-short">Devices</span>
          </div>
          <Bar value={deviceFill} className="overview-qs-tile__bar overview-qs-tile__bar--dev" />
        </a>
      </div>
    </section>
  );
};

export default QuickStatsCard;
