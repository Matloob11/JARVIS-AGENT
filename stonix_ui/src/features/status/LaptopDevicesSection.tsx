import { useEffect, useState } from 'react';
import { Pill } from '@/shared/ui/Pill';
import { StopDesktopButton } from '@/features/controls/StopDesktopButton';
import {
  formatDesktopLastSeen,
  formatDesktopMode,
  type DesktopStatusSnapshot,
} from './desktopStatusStore';
import { deriveLaptopChipPresentation } from './laptopChipPresentation';

export type LaptopDevicesSectionProps = {
  desktop: DesktopStatusSnapshot;
};

export function LaptopDevicesSection({ desktop }: LaptopDevicesSectionProps) {
  const [nowMs, setNowMs] = useState(() => Date.now());
  const chip = deriveLaptopChipPresentation(desktop);

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const lastSeen = formatDesktopLastSeen(desktop.lastSeenMs, nowMs);
  const modeLabel = formatDesktopMode(desktop.mode);
  const lockedLabel = desktop.seen ? (desktop.locked ? 'Yes' : 'No') : '—';
  const voiceLabel = desktop.seen ? (desktop.voiceActive ? 'Yes' : 'No') : '—';
  const busyLabel = desktop.busyTool?.trim() ? desktop.busyTool : 'none';
  const rttLabel =
    desktop.rttMsP50 != null && Number.isFinite(desktop.rttMsP50)
      ? `${desktop.rttMsP50} ms`
      : 'n/a';

  return (
    <div className="neon-pop-sec" data-testid="chip-laptop">
      <b>
        Laptop (Desktop Node){' '}
        <Pill tone={chip.tone} status={chip.statusKind} statusLabel={chip.long}>
          {chip.short}
        </Pill>
      </b>
      <dl className="neon-pop-kv">
        <dt>Mode</dt>
        <dd>{modeLabel}</dd>
        <dt>Locked</dt>
        <dd>{lockedLabel}</dd>
        <dt>Voice active</dt>
        <dd>{voiceLabel}</dd>
        <dt>Busy tool</dt>
        <dd>{busyLabel}</dd>
        <dt>Last seen</dt>
        <dd>{desktop.seen ? lastSeen : 'unknown'}</dd>
        <dt>Round trip</dt>
        <dd>{rttLabel}</dd>
      </dl>
      <StopDesktopButton desktop={desktop} testId="desktop-stop-menu" className="neon-pop-foot" />
      <div className="neon-pop-foot">
        <a className="neon-pop-link" href="#/agents/laptop">
          Open Agents › Laptop
        </a>
      </div>
    </div>
  );
}

export default LaptopDevicesSection;
