import { useEffect, useState, type RefObject } from 'react';
import { Wifi } from 'lucide-react';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import { formatLastSeen, useAgentStatusStore } from '@/features/status/agentStatusStore';
import { BRIDGE_BASE } from '@/shared/config/runtime';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import type { CameraState } from '@/features/command-center/components/cameraState';
import { cameraBadgeLabel } from '@/features/command-center/components/cameraState';
import { pullVortexStatus, type VortexStatusSnapshot } from '../useVortexStatusPoll';
import { Popover } from './Popover';

function agentShortLabel(agent: { online: boolean; paused: boolean; restarting: boolean }): string {
  if (agent.restarting) return 'Restarting';
  if (agent.paused) return 'Paused';
  if (!agent.online) return 'Offline';
  return 'Online';
}

function voiceRoomLabel(voice: ReturnType<typeof useLiveKit>): string {
  if (voice.linkedElsewhere || voice.duplicateBlocked) return 'Linked to another device';
  if (voice.roomState === 'connecting' || voice.roomState === 'reconnecting') return 'Joining…';
  if (voice.voiceUnavailable || voice.roomState === 'error') return 'Unavailable';
  if (voice.roomState === 'connected') return 'Joined';
  return 'Offline';
}

function cameraLine(camera: CameraState): string {
  if (!camera.enabled) return 'Off';
  if (camera.sharing) return 'On, sharing with agent';
  if (camera.badge === 'local_preview') return 'On, local preview only';
  if (camera.badge === 'livekit') return 'On, sharing with agent';
  return cameraBadgeLabel(camera.badge);
}

export type DevicesPopoverProps = {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
  sheet: boolean;
  bridgeConnected: boolean;
  deviceCount: number;
  vortex: VortexStatusSnapshot;
  onReconnect: () => void | Promise<void>;
  cameraState: CameraState;
};

export function DevicesPopover({
  open,
  onClose,
  triggerRef,
  sheet,
  bridgeConnected,
  deviceCount,
  vortex,
  onReconnect,
  cameraState,
}: DevicesPopoverProps) {
  const voice = useLiveKit();
  const agent = useAgentStatus();
  const { status } = useAgentStatusStore();
  const [detail, setDetail] = useState<VortexStatusSnapshot | null>(null);
  const nowMs = Date.now();

  useEffect(() => {
    if (!open) return;
    void pullVortexStatus().then(setDetail);
  }, [open]);

  const merged = detail ? { ...vortex, ...detail } : vortex;
  const basePath = BRIDGE_BASE.startsWith('http') ? '/vortex' : BRIDGE_BASE || '/vortex';
  const lastSeen = formatLastSeen(status.lastSeenMs, nowMs).replace(/^last seen /, '');

  return (
    <Popover
      open={open}
      onClose={onClose}
      triggerRef={triggerRef}
      ariaLabel="Connected devices"
      data-testid="devices-menu"
      sheet={sheet}
    >
      <div className="neon-pop-hd">
        <b>Connected devices</b>
        <span className="n-pill" data-tone={bridgeConnected ? 'ok' : 'err'}>
          {bridgeConnected ? `${deviceCount} online` : 'Bridge offline'}
        </span>
      </div>

      <div className="neon-pop-sec">
        <b>
          This browser{' '}
          <span className="n-pill" data-tone={bridgeConnected ? 'ok' : 'err'}>
            {bridgeConnected ? 'Live' : 'Offline'}
          </span>
        </b>
        <dl className="neon-pop-kv">
          <dt>Bridge</dt>
          <dd>{basePath}</dd>
          <dt>Voice room</dt>
          <dd>{voiceRoomLabel(voice)}</dd>
          <dt>Camera</dt>
          <dd>{cameraLine(cameraState)}</dd>
        </dl>
        <div className="neon-pop-foot">
          <button
            type="button"
            className="n-btn quiet sm topbar-reconnect-btn"
            data-testid="topbar-reconnect"
            title="Reconnect: rejoins bridge and voice without reloading the dashboard"
            onClick={() => void onReconnect()}
          >
            <Wifi size={15} />
            Reconnect
          </button>
          {voice.linkedElsewhere || voice.duplicateBlocked ? (
            <button type="button" className="n-btn sm" onClick={() => void voice.takeOver()}>
              Take over
            </button>
          ) : null}
        </div>
      </div>

      <div className="neon-pop-sec">
        <b>
          Agent (VPS){' '}
          <span className="n-pill" data-tone={agent.online && !agent.paused ? 'ok' : 'warn'}>
            {agentShortLabel(agent)}
          </span>
        </b>
        <p className="neon-pop-note m-0">Last seen {bridgeConnected ? lastSeen : '—'}</p>
        <div className="neon-pop-foot">
          <a className="neon-pop-link" href="#/agents/agent">
            Open Agents ›
          </a>
        </div>
      </div>

      {merged.laptopOnline != null ? (
        <div className="neon-pop-sec">
          <b>Laptop (Desktop Node)</b>
          <p className="neon-pop-note">Laptop details arrive with a future update.</p>
        </div>
      ) : null}
    </Popover>
  );
}
