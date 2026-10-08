import { forwardRef, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Mic, MicOff, Monitor, Volume2 } from 'lucide-react';
import type { CameraState } from '@/features/command-center/components/cameraState';
import type { VoiceMatchLive } from '@/features/voice/voiceMeter';
import { useSettings } from '@/features/settings/useSettings';
import { formatLastSeen, useAgentStatusStore } from '@/features/status/agentStatusStore';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import type { AgentStatusSnapshot } from '@/features/status/agentStatusStore';
import { useAgentListeningMuted } from '@/features/status/agentListeningStore';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { getPersona } from '@/shared/persona';
import type { LayoutMode } from './useLayout';
import { connectedDeviceCount, useVortexStatusPoll } from './useVortexStatusPoll';
import { agentHostTitle } from '@/features/status/agentHostLabel';
import { useDesktopStatus } from '@/features/status/useDesktopStatus';
import { deriveLaptopChipPresentation, type LaptopChipShape } from '@/features/status/laptopChipPresentation';
import { deriveVoicePillCopy } from './voicePillState';
import { useE2EVoiceTick } from '@/features/voice/voiceState';
import { AgentPopover } from './popovers/AgentPopover';
import { DevicesPopover } from './popovers/DevicesPopover';
import { VoicePopover } from './popovers/VoicePopover';

type StatusPillsProps = {
  bridgeConnected: boolean;
  compact: boolean;
  narrow?: boolean;
  isUserSpeaking: boolean;
  layout: LayoutMode;
  onReconnect: () => void | Promise<void>;
  cameraState: CameraState;
  voiceMatchLive: VoiceMatchLive;
  onOpenVoiceEnroll: () => void;
};

type OpenPop = 'agent' | 'devices' | 'voice' | null;

function agentSystemPillCopy(
  bridgeConnected: boolean,
  agentStatusSeen: boolean,
  agent: Pick<AgentStatusSnapshot, 'online' | 'paused' | 'restarting'>,
): { full: string; compact: string; active: boolean; shape: 'online' | 'warn' | 'err' | 'unknown' | 'spinner' } {
  if (!bridgeConnected) {
    return { full: 'System Unreachable', compact: 'Unreachable', active: false, shape: 'warn' };
  }
  if (!agentStatusSeen) {
    return { full: 'System Unknown', compact: 'Unknown', active: false, shape: 'unknown' };
  }
  if (agent.restarting) {
    return { full: 'System Restarting', compact: 'Restarting', active: false, shape: 'spinner' };
  }
  if (agent.paused) {
    return { full: 'System Paused', compact: 'Paused', active: false, shape: 'warn' };
  }
  if (!agent.online) {
    return { full: 'System Offline', compact: 'Offline', active: false, shape: 'err' };
  }
  return { full: 'System Online', compact: 'Online', active: true, shape: 'online' };
}

function DevicesPillMarks({
  shape,
  cameraOn,
}: {
  shape: LaptopChipShape;
  cameraOn: boolean;
}) {
  const mark =
    shape === 'panic'
      ? 'panic'
      : shape === 'stale' || shape === 'locked'
        ? 'warn'
        : shape === 'readonly'
          ? 'readonly'
          : null;
  return (
    <span className="header-pill-marks" aria-hidden>
      {mark === 'panic' ? <span className="n-sh" data-s="err" data-laptop-mark="panic" /> : null}
      {mark === 'warn' ? <span className="n-sh" data-s="warn" data-laptop-mark="stale" /> : null}
      {mark === 'readonly' ? <span className="n-sh" data-s="dev" data-laptop-mark="readonly" /> : null}
      {cameraOn ? <span className="n-sh" data-s="rec" data-laptop-mark="camera" /> : null}
    </span>
  );
}
function AgentPillShape({ shape }: { shape: 'online' | 'warn' | 'err' | 'unknown' | 'spinner' }) {
  if (shape === 'spinner') {
    return <Loader2 size={16} className="header-pill-spin animate-spin" aria-hidden />;
  }
  if (shape === 'online') {
    return <span className="dot-online dot-online--ok" aria-hidden />;
  }
  if (shape === 'unknown') {
    return <span className="dot-online dot-online--unknown" aria-hidden />;
  }
  if (shape === 'warn') {
    return <span className="dot-online" data-s="warn" aria-hidden />;
  }
  return <span className="dot-online" data-s="err" aria-hidden />;
}

function useNowMsMinute(): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 60_000);
    return () => window.clearInterval(id);
  }, []);
  return nowMs;
}

export function StatusPills({
  bridgeConnected,
  compact,
  narrow = false,
  isUserSpeaking,
  layout,
  onReconnect,
  cameraState,
  voiceMatchLive,
  onOpenVoiceEnroll,
}: StatusPillsProps) {
  const { status, agentStatusSeen } = useAgentStatusStore();
  const agent = useAgentStatus();
  const vortex = useVortexStatusPoll(true);
  const desktop = useDesktopStatus();
  const nowMs = useNowMsMinute();
  const sheet = layout === 'ph';

  const [openPop, setOpenPop] = useState<OpenPop>(null);
  const agentRef = useRef<HTMLButtonElement>(null);
  const devicesRef = useRef<HTMLButtonElement>(null);
  const voiceRef = useRef<HTMLButtonElement>(null);

  const togglePop = (id: OpenPop) => {
    setOpenPop(prev => (prev === id ? null : id));
  };

  const laptopForCount = desktop.seen ? desktop.online : vortex.laptopOnline;
  const deviceCount = useMemo(
    () => connectedDeviceCount(vortex.uiClients, status.online, laptopForCount),
    [vortex.uiClients, laptopForCount, status.online],
  );
  const laptopChip = deriveLaptopChipPresentation(desktop);

  const agentTitle = useMemo(() => {
    const last = formatLastSeen(status.lastSeenMs, nowMs);
    return `${agentHostTitle(desktop.brain)}, ${last}`;
  }, [desktop.brain, status.lastSeenMs, nowMs]);

  const agentPill = agentSystemPillCopy(bridgeConnected, agentStatusSeen, agent);
  const agentLabel = compact ? agentPill.compact : agentPill.full;
  const agentShape: typeof agentPill.shape =
    agentPill.shape === 'spinner' && agent.restarting ? 'spinner' : agentPill.shape === 'spinner' ? 'unknown' : agentPill.shape;

  return (
    <div className="header-pills">
      <div className="header-pill-wrap">
        <button
          ref={agentRef}
          type="button"
          className="header-pill edge header-pill--system"
          data-testid="chip-agent"
          data-active={agentPill.active ? 'true' : 'false'}
          title={agentTitle}
          aria-haspopup="dialog"
          aria-expanded={openPop === 'agent'}
          onClick={() => togglePop('agent')}
        >
          <AgentPillShape shape={agentShape} />
          <span className="header-pill-lbl">{agentLabel}</span>
        </button>
        <AgentPopover
          open={openPop === 'agent'}
          onClose={() => setOpenPop(null)}
          triggerRef={agentRef}
          sheet={sheet}
          bridgeConnected={bridgeConnected}
          vortex={vortex}
        />
      </div>

      <div className="header-pill-wrap">
        <BridgeDevicesPill
          ref={devicesRef}
          bridgeConnected={bridgeConnected}
          compact={compact}
          narrow={narrow}
          deviceCount={deviceCount}
          laptopMark={laptopChip.shape}
          cameraOn={cameraState.enabled}
          expanded={openPop === 'devices'}
          onToggle={() => togglePop('devices')}
        />
        <DevicesPopover
          open={openPop === 'devices'}
          onClose={() => setOpenPop(null)}
          triggerRef={devicesRef}
          sheet={sheet}
          bridgeConnected={bridgeConnected}
          deviceCount={deviceCount}
          vortex={vortex}
          onReconnect={onReconnect}
          cameraState={cameraState}
        />
      </div>

      <div className="header-pill-wrap">
        <VoiceStatusPill
          ref={voiceRef}
          compact={compact}
          isUserSpeaking={isUserSpeaking}
          expanded={openPop === 'voice'}
          onToggle={() => togglePop('voice')}
        />
        <VoicePopover
          open={openPop === 'voice'}
          onClose={() => setOpenPop(null)}
          triggerRef={voiceRef}
          sheet={sheet}
          isUserSpeaking={isUserSpeaking}
          voiceMatchLive={voiceMatchLive}
          onOpenVoiceEnroll={onOpenVoiceEnroll}
        />
      </div>
    </div>
  );
}

const BridgeDevicesPill = forwardRef<
  HTMLButtonElement,
  {
    bridgeConnected: boolean;
    compact: boolean;
    narrow: boolean;
    deviceCount: number;
    laptopMark: LaptopChipShape;
    cameraOn: boolean;
    expanded: boolean;
    onToggle: () => void;
  }
>(function BridgeDevicesPill(
  { bridgeConnected, compact, narrow, deviceCount, laptopMark, cameraOn, expanded, onToggle },
  ref,
) {
  const label = useMemo(() => {
    if (!bridgeConnected) {
      return compact ? 'Offline' : 'Bridge Offline';
    }
    if (narrow && bridgeConnected) {
      return String(deviceCount);
    }
    if (compact) {
      return deviceCount === 1 ? '1 Device' : `${deviceCount} Devices`;
    }
    if (deviceCount === 1) return '1 Connected Device';
    return `${deviceCount} Connected Devices`;
  }, [bridgeConnected, compact, deviceCount, narrow]);

  const ariaLabel =
    bridgeConnected && (narrow || compact) ? `${deviceCount} connected devices` : undefined;

  return (
    <button
      ref={ref}
      type="button"
      className="header-pill edge header-pill--devices"
      data-testid="chip-bridge"
      data-active={bridgeConnected ? 'true' : 'false'}
      aria-haspopup="dialog"
      aria-expanded={expanded}
      aria-label={ariaLabel}
      title={bridgeConnected ? `${deviceCount} connected devices` : 'Bridge offline'}
      onClick={onToggle}
    >
      <Monitor className="header-pill-ic" size={20} aria-hidden />
      <DevicesPillMarks shape={laptopMark} cameraOn={cameraOn} />
      <span className="header-pill-lbl">{label}</span>
    </button>
  );
});

const VoiceStatusPill = forwardRef<
  HTMLButtonElement,
  {
    compact: boolean;
    isUserSpeaking: boolean;
    expanded: boolean;
    onToggle: () => void;
  }
>(function VoiceStatusPill({ compact, isUserSpeaking, expanded, onToggle }, ref) {
  const liveKit = useLiveKit();
  const agentMuted = useAgentListeningMuted();
  const { status } = useAgentStatusStore();
  const settings = useSettings();
  const voiceName = getPersona(settings.persona).voice;
  const e2eTick = useE2EVoiceTick();

  const copy = useMemo(() => {
    void e2eTick;
    return deriveVoicePillCopy(liveKit, status, agentMuted, isUserSpeaking, settings.persona);
  }, [liveKit, status, agentMuted, isUserSpeaking, settings.persona, e2eTick]);

  const Icon =
    copy.visual === 'offline' || copy.visual === 'agent-absent' || copy.visual === 'audio-blocked'
      ? Volume2
      : copy.visual === 'muted'
        ? MicOff
        : Mic;

  return (
    <button
      ref={ref}
      type="button"
      className="header-pill edge header-pill--voice"
      data-testid="chip-voice"
      data-active={copy.dataActive ? 'true' : 'false'}
      aria-haspopup="dialog"
      aria-expanded={expanded}
      title={`Voice: ${copy.full}. Voice ${voiceName}`}
      onClick={onToggle}
    >
      {copy.spinner ? (
        <Loader2 size={16} className="header-pill-spin animate-spin" aria-hidden />
      ) : (
        <Icon className="header-pill-ic" size={20} aria-hidden />
      )}
      {copy.ping ? <span className="header-pill-ping" aria-hidden /> : null}
      <span className="header-pill-lbl">{compact ? copy.compact : copy.full}</span>
    </button>
  );
});
