import { useEffect, useState } from 'react';
import { MapPin } from 'lucide-react';
import { Card } from '@/shared/ui/Card';
import { Pill } from '@/shared/ui/Pill';
import { personaAccent } from '@/shared/theme/commandTheme';
import { toEpochMs } from '@/shared/contracts/events';
import type { LocationData } from '@/features/command-center/hooks/useCommandCenter';
import { useDesktopStatus } from '@/features/status/useDesktopStatus';
import {
  formatDesktopLastSeen,
  formatDesktopMode,
} from '@/features/status/desktopStatusStore';
import { deriveLaptopChipPresentation } from '@/features/status/laptopChipPresentation';
import { StopDesktopButton } from '@/features/controls/StopDesktopButton';

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

export type LaptopCardProps = {
  persona: 'jarvis' | 'anna';
  location: LocationData;
};

export function LaptopCard({ persona, location }: LaptopCardProps) {
  const accent = personaAccent(persona);
  const desktop = useDesktopStatus();
  const chip = deriveLaptopChipPresentation(desktop);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const stateLine = desktop.seen ? chip.long : 'unknown';
  const modeLine = desktop.seen ? formatDesktopMode(desktop.mode) : '—';
  const lockedLine = desktop.seen ? (desktop.locked ? 'Yes' : 'No') : '—';
  const voiceLine = desktop.seen ? (desktop.voiceActive ? 'Yes' : 'No') : '—';
  const busyLine = desktop.busyTool?.trim() ? desktop.busyTool : 'none';
  const versionLine =
    desktop.version?.trim() ||
    (desktop.gitSha ? desktop.gitSha.slice(0, 8) : '') ||
    'unknown';
  const manifestLine =
    desktop.manifestOk === true ? 'ok' : desktop.manifestOk === false ? 'fail' : '—';
  const brainLine = desktop.brain?.trim() ? desktop.brain : '—';
  const rttLine =
    desktop.rttMsP50 != null && Number.isFinite(desktop.rttMsP50)
      ? `${desktop.rttMsP50} ms`
      : 'n/a';
  const lastSeenLine = desktop.seen ? formatDesktopLastSeen(desktop.lastSeenMs, nowMs) : 'never';

  return (
    <div data-testid="laptop-card" data-anchor="laptop" className="agents-anchor" data-shape={chip.shape}>
      <Card
        icon="laptop"
        title="Laptop (Desktop Node)"
        subtitle="Apps, files, screenshots and typing on your PC."
        boundaryName="Laptop (Desktop Node)"
        className="agents-card agents-card--laptop"
        action={
          <Pill tone={chip.tone} status={chip.statusKind} statusLabel={chip.long}>
            {chip.short}
          </Pill>
        }
      >
        <dl className="agents-kv">
          <dt>State</dt>
          <dd>{stateLine}</dd>
          <dt>Control mode</dt>
          <dd>{modeLine}</dd>
          <dt>Locked</dt>
          <dd>{lockedLine}</dd>
          <dt>Voice active</dt>
          <dd>{voiceLine}</dd>
          <dt>Busy tool</dt>
          <dd>{busyLine}</dd>
          <dt>Brain</dt>
          <dd>{brainLine}</dd>
          <dt>Version</dt>
          <dd>
            {versionLine}
            {desktop.seen ? ` · manifest ${manifestLine}` : ''}
          </dd>
          <dt>Round trip</dt>
          <dd>{rttLine}</dd>
          <dt>Last seen</dt>
          <dd>{lastSeenLine}</dd>
        </dl>

        {!desktop.seen ? (
          <p className="agents-vid-note">
            No report from the laptop yet. It counts once it reports.
          </p>
        ) : chip.shape === 'readonly' ? (
          <p className="agents-vid-note">
            Read-only until the laptop tray switches back to Full.
          </p>
        ) : chip.shape === 'panic' ? (
          <p className="agents-vid-note">
            Panic: desktop control is halted until the laptop recovers.
          </p>
        ) : null}

        <StopDesktopButton desktop={desktop} testId="desktop-stop" className="mt-3" />

        <div className="location-card agents-laptop-loc" data-testid="device-location-card">
          <MapPin size={16} className={accent.text} />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-command-text">{location.city || 'Detecting...'}</p>
            <p className="mt-1 font-mono text-[11px] text-command-muted">
              {formatCoords(location.lat, location.lng)}
            </p>
            {location.ts != null ? (
              <p className="mt-1 text-[10px] text-command-muted" data-testid="device-location-updated">
                {formatLocationUpdated(location.ts, nowMs)}
              </p>
            ) : null}
          </div>
        </div>
      </Card>
    </div>
  );
}

export default LaptopCard;
