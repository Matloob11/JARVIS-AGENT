import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, Cpu, HardDrive, MapPin, MemoryStick } from 'lucide-react';
import CameraPanel from './CameraPanel';
import type { CameraState } from './cameraState';
import MapFocus, { type MapFocusData } from '@/features/location/MapFocus';
import {
  LocationData,
  SystemAlert,
  VitalsSnapshot,
  VortexLog,
} from '../hooks/useCommandCenter';
import TelemetryCard, { TelemetrySnapshot } from '@/features/telemetry/TelemetryCard';
import { Panel, StatusDot } from '@/shared/ui/Panel';
import { personaAccent } from '@/shared/theme/commandTheme';
import { toEpochMs } from '@/shared/contracts/events';

const STALE_MS = 30_000;
const ALERT_LEVELS = new Set(['warning', 'error', 'critical']);

interface OperationsRailProps {
  persona: 'jarvis' | 'anna';
  location: LocationData;
  onCameraStateChange?: (state: CameraState) => void;
  mapFocus: MapFocusData | null;
  vitalsPc: VitalsSnapshot;
  vitalsVps: VitalsSnapshot;
  telemetry: TelemetrySnapshot;
  systemAlerts: SystemAlert[];
  logs: VortexLog[];
}

function metricValue(value: number): number {
  return Number.isFinite(value) ? value : 0;
}

function vitalsAgeMs(vitals: VitalsSnapshot, nowMs: number): number {
  const ts = vitals.updatedAtMs;
  if (typeof ts !== 'number' || !Number.isFinite(ts)) return Number.POSITIVE_INFINITY;
  return Math.max(0, nowMs - ts);
}

function formatAgo(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return 'unknown';
  if (seconds < 1) return 'just now';
  return `${Math.floor(seconds)}s ago`;
}

function formatLocationUpdated(ts: unknown, nowMs: number): string {
  const ms = toEpochMs(ts);
  if (ms === undefined) return '';
  const sec = Math.max(0, Math.floor((nowMs - ms) / 1000));
  if (sec < 60) return 'updated just now';
  const min = Math.floor(sec / 60);
  if (min === 1) return 'updated 1 min ago';
  return `updated ${min} min ago`;
}

function formatCoords(lat: number, lng: number): string {
  const latOk = Number.isFinite(lat);
  const lngOk = Number.isFinite(lng);
  if (!latOk && !lngOk) return '—';
  if (latOk && lngOk) return `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  return latOk ? lat.toFixed(4) : lng.toFixed(4);
}

function resolveLogLevel(log: VortexLog): string | null {
  const level = log.level?.toLowerCase();
  if (level && ALERT_LEVELS.has(level)) return level.toUpperCase();
  const category = log.category?.toLowerCase();
  if (category && ALERT_LEVELS.has(category)) return category.toUpperCase();
  return null;
}

function formatLastSeen(ts: unknown): string {
  const ms = toEpochMs(ts);
  if (ms === undefined) return '';
  const ago = Math.floor((Date.now() - ms) / 1000);
  return ago >= 0 ? `${ago}s ago` : '';
}

const OperationsRail = ({
  persona,
  location,
  onCameraStateChange,
  mapFocus,
  vitalsPc,
  vitalsVps,
  telemetry,
  systemAlerts,
  logs,
}: OperationsRailProps) => {
  const accent = personaAccent(persona);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const logAlerts = useMemo(
    () =>
      logs
        .map(log => ({ log, level: resolveLogLevel(log) }))
        .filter((entry): entry is { log: VortexLog; level: string } => entry.level != null)
        .slice(0, 20),
    [logs],
  );

  const hasAlerts = systemAlerts.length > 0 || logAlerts.length > 0;

  return (
    <aside className="operations-rail">
      <CameraPanel onCameraStateChange={onCameraStateChange} />

      <Panel title="Location">
        <div className="location-card" data-testid="device-location-card">
          <MapPin size={16} className={accent.text} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-command-text">{location.city || 'Detecting...'}</p>
            <p className="mt-1 font-mono text-[11px] text-command-muted">
              {formatCoords(location.lat, location.lng)}
            </p>
            {location.ts != null ? (
              <p
                className="mt-1 text-[10px] text-command-muted"
                data-testid="device-location-updated"
              >
                {formatLocationUpdated(location.ts, nowMs)}
              </p>
            ) : null}
          </div>
        </div>
      </Panel>

      <Panel title="Map">
        <MapFocus focus={mapFocus} />
      </Panel>

      <Panel title="Vitals">
        <div className="space-y-5">
          <VitalsBlock
            testId="vitals-pc"
            title="PC (agent)"
            vitals={vitalsPc}
            diskLabel={
              vitalsPc.disk_label ? `Disk (${vitalsPc.disk_label})` : 'Disk (C:)'
            }
            nowMs={nowMs}
          />
          <TelemetryCard telemetry={telemetry} />
          <VitalsBlock
            testId="vitals-vps"
            title="VPS (server)"
            vitals={vitalsVps}
            diskLabel={vitalsVps.disk_label ? `Disk (${vitalsVps.disk_label})` : 'Disk'}
            nowMs={nowMs}
          />
        </div>
      </Panel>

      <Panel title="Alerts" className="min-h-0 flex-1">
        <div className="space-y-2">
          {!hasAlerts ? (
            <div className="empty-compact">
              <StatusDot active tone="accent" />
              Normal
            </div>
          ) : (
            <>
              {systemAlerts.slice(0, 10).map((alert, index) => {
                const count = alert.xcount ?? alert.count ?? 1;
                const lastSeen = formatLastSeen(alert.last_seen ?? alert.ts);
                return (
                  <div
                    key={alert.id ?? `${alert.title}-${index}`}
                    className="alert-row"
                    data-testid="system-alert-row"
                  >
                    <AlertTriangle size={13} className="mt-0.5 text-command-warning" />
                    <div className="min-w-0">
                      <p className="font-semibold text-command-text">
                        <span className="mr-2 uppercase text-[10px] text-command-warning">
                          {alert.severity || 'alert'}
                        </span>
                        {alert.title}
                      </p>
                      <p className="text-[10px] text-command-muted">
                        {count > 1 ? `×${count}` : null}
                        {count > 1 && lastSeen ? ' · ' : null}
                        {lastSeen ? `last ${lastSeen}` : null}
                      </p>
                    </div>
                  </div>
                );
              })}
              {logAlerts.slice(0, 10).map(({ log, level }, index) => (
                <div
                  key={`${log.timestamp}-${index}`}
                  className="alert-row"
                  data-testid="log-alert-row"
                  data-level={level}
                >
                  <AlertTriangle size={13} className="mt-0.5 text-command-warning" />
                  <div className="min-w-0">
                    <span className="mr-2 text-[10px] font-semibold uppercase text-command-warning">{level}</span>
                    <span>{log.text}</span>
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </Panel>
    </aside>
  );
};

const VitalsBlock = ({
  testId,
  title,
  vitals,
  diskLabel,
  nowMs,
}: {
  testId: string;
  title: string;
  vitals: VitalsSnapshot;
  diskLabel: string;
  nowMs: number;
}) => {
  const ageMs = vitalsAgeMs(vitals, nowMs);
  const stale = ageMs > STALE_MS;
  const agoSec = ageMs / 1000;

  return (
    <div data-testid={testId} className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold text-command-text">{title}</p>
        <div className="flex items-center gap-2 text-[10px] text-command-muted">
          <span data-testid={`${testId}-updated`}>updated {formatAgo(agoSec)}</span>
          {stale ? (
            <span
              className="rounded border border-command-warning/40 bg-command-warning/10 px-1.5 py-0.5 uppercase tracking-wide text-command-warning"
              data-testid={`${testId}-stale`}
            >
              stale
            </span>
          ) : null}
        </div>
      </div>
      <Metric icon={Cpu} label="CPU" value={metricValue(vitals.cpu)} />
      <Metric icon={MemoryStick} label="Memory" value={metricValue(vitals.ram)} />
      <Metric icon={HardDrive} label={diskLabel} value={metricValue(vitals.disk)} />
    </div>
  );
};

const Metric = ({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Cpu;
  label: string;
  value: number;
}) => (
  <div>
    <div className="mb-2 flex items-center justify-between">
      <div className="flex items-center gap-2 text-xs text-command-muted">
        <Icon size={13} />
        {label}
      </div>
      <span className="font-mono text-xs text-command-text">{value.toFixed(1)}%</span>
    </div>
    <div className="metric-track">
      <div className="metric-fill" style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
    </div>
  </div>
);

export default OperationsRail;
