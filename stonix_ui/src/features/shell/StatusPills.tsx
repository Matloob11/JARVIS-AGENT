import { useEffect, useMemo, useState } from 'react';
import { Loader2, Mic, MicOff, Monitor, Volume2 } from 'lucide-react';
import { useSettings } from '@/features/settings/useSettings';
import { AgentChipMenu } from '@/features/status/AgentControls';
import { formatLastSeen, useAgentStatusStore } from '@/features/status/agentStatusStore';
import { useAgentListeningMuted } from '@/features/status/agentListeningStore';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { getPersona } from '@/shared/persona';
import { connectedDeviceCount, useVortexStatusPoll } from './useVortexStatusPoll';
import { deriveVoicePillCopy } from './voicePillState';

type StatusPillsProps = {
  bridgeConnected: boolean;
  compact: boolean;
  narrow?: boolean;
  isUserSpeaking: boolean;
};

function BridgeDevicesPill({
  bridgeConnected,
  compact,
  narrow,
  deviceCount,
}: {
  bridgeConnected: boolean;
  compact: boolean;
  narrow: boolean;
  deviceCount: number;
}) {
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
      type="button"
      className="header-pill edge header-pill--devices"
      data-testid="chip-bridge"
      data-active={bridgeConnected ? 'true' : 'false'}
      aria-haspopup="dialog"
      aria-expanded={false}
      aria-label={ariaLabel}
      title={bridgeConnected ? `${deviceCount} connected devices` : 'Bridge offline'}
    >
      <Monitor className="header-pill-ic" size={20} aria-hidden />
      <span className="header-pill-lbl">{label}</span>
    </button>
  );
}

function VoiceStatusPill({
  compact,
  isUserSpeaking,
}: {
  compact: boolean;
  isUserSpeaking: boolean;
}) {
  const liveKit = useLiveKit();
  const agentMuted = useAgentListeningMuted();
  const { status } = useAgentStatusStore();
  const settings = useSettings();
  const voiceName = getPersona(settings.persona).voice;

  const copy = useMemo(
    () => deriveVoicePillCopy(liveKit, status, agentMuted, isUserSpeaking),
    [liveKit, status, agentMuted, isUserSpeaking],
  );

  const Icon =
    copy.visual === 'offline' || copy.visual === 'agent-absent' || copy.visual === 'audio-blocked'
      ? Volume2
      : copy.visual === 'mic-off' || copy.visual === 'muted'
        ? MicOff
        : Mic;

  return (
    <button
      type="button"
      className="header-pill edge header-pill--voice"
      data-testid="chip-voice"
      data-active={copy.dataActive ? 'true' : 'false'}
      aria-haspopup="dialog"
      aria-expanded={false}
      title={`Voice: ${copy.full}. Voice ${voiceName}`}
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
}: StatusPillsProps) {
  const { status, agentStatusSeen } = useAgentStatusStore();
  const vortex = useVortexStatusPoll(true);
  const nowMs = useNowMsMinute();

  const deviceCount = useMemo(
    () => connectedDeviceCount(vortex.uiClients, status.online, vortex.laptopOnline),
    [vortex.uiClients, vortex.laptopOnline, status.online],
  );

  const agentTitle = useMemo(() => {
    const last = formatLastSeen(status.lastSeenMs, nowMs);
    return `Agent (VPS), ${last}`;
  }, [status.lastSeenMs, nowMs]);

  return (
    <div className="header-pills">
      <AgentChipMenu
        bridgeConnected={bridgeConnected}
        agentStatusSeen={agentStatusSeen}
        compact={compact}
        title={agentTitle}
      />
      <BridgeDevicesPill
        bridgeConnected={bridgeConnected}
        compact={compact}
        narrow={narrow}
        deviceCount={deviceCount}
      />
      <VoiceStatusPill compact={compact} isUserSpeaking={isUserSpeaking} />
    </div>
  );
}
