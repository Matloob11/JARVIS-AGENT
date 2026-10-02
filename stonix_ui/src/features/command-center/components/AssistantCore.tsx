import { useEffect, useState } from 'react';
import { Activity, Ear, EarOff, Power, ShieldCheck } from 'lucide-react';
import { personaAccent } from '@/shared/theme/commandTheme';
import { useSettings } from '@/features/settings/useSettings';
import { deriveVoiceMeterView, type VoiceMatchLive } from '@/features/voice/voiceMeter';
import { emitUiCommandWithAck, useAgentListeningMuted } from '@/features/status/AgentControls';
import { listeningMutedTitle } from '@/shared/persona';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import { useLiveKit } from '@/features/voice/LiveKitProvider';

interface AssistantCoreProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  isThinking: boolean;
  voiceMatchLive: VoiceMatchLive;
  onOpenVoiceEnroll: () => void;
  sessionRestartBanner: boolean;
  onSessionRestartBannerChange: (active: boolean) => void;
}

const METER_TONE_CLASS: Record<string, string> = {
  muted: 'text-command-muted',
  monitor: 'text-sky-400',
  pass: 'text-command-accent',
  fail: 'text-command-danger',
};

const AssistantCore = ({
  persona,
  isConnected,
  isThinking,
  voiceMatchLive,
  onOpenVoiceEnroll,
  sessionRestartBanner,
  onSessionRestartBannerChange,
}: AssistantCoreProps) => {
  const accent = personaAccent(persona);
  const settings = useSettings();
  const agentStatus = useAgentStatus();
  const voice = useLiveKit();
  const [shutdownAgentSupported, setShutdownAgentSupported] = useState(true);
  const meter = deriveVoiceMeterView(
    {
      voiceId: settings.voiceId,
      voiceIdEnrolled: settings.voiceIdEnrolled,
      voiceIdThreshold: settings.voiceIdThreshold,
      enforcedVoiceId: settings.enforcedVoiceId,
    },
    voiceMatchLive,
  );
  const agentMuted = useAgentListeningMuted();
  const voiceConnecting = voice.roomState === 'connecting' || voice.roomState === 'reconnecting';
  const voiceLive = voice.roomState === 'connected';
  const agentLabel = voice.agentState ? voice.agentState : voiceLive ? 'linked' : 'offline';

  useEffect(() => {
    if (agentStatus.restarting) {
      onSessionRestartBannerChange(true);
    }
  }, [agentStatus.restarting, onSessionRestartBannerChange]);

  useEffect(() => {
    if (sessionRestartBanner && agentStatus.online && !agentStatus.restarting) {
      onSessionRestartBannerChange(false);
    }
  }, [
    sessionRestartBanner,
    agentStatus.online,
    agentStatus.restarting,
    onSessionRestartBannerChange,
  ]);

  const onTerminateSession = () => {
    emitUiCommandWithAck('shutdown_agent', { restart: true }, ack => {
      if (ack?.status === 'rejected') {
        setShutdownAgentSupported(false);
      }
    });
  };

  const stateLabel = voiceConnecting
    ? 'Joining voice…'
    : voice.voiceUnavailable
      ? 'Voice unavailable'
      : voiceLive
        ? `Voice (${voice.roomName || 'jarvis-hybrid'}) · ${agentLabel}`
        : isThinking
          ? 'Processing'
          : isConnected
            ? 'System active'
            : 'Offline';

  return (
    <div className="overview-legacy-strip assistant-core assistant-core--legacy" data-legacy>
      {sessionRestartBanner ? (
        <div
          className="assistant-core__restart-banner"
          data-testid="agent-session-restart-banner"
          role="status"
        >
          Agent session restart ho raha hai
        </div>
      ) : null}

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[11px] font-semibold text-command-text">
          <Activity size={14} className={accent.text} />
          {stateLabel}
        </div>
        <p className="mt-1 text-[11px] text-command-muted">
          {voice.voiceUnavailable
            ? 'LiveKit abhi available nahi — chat aur bridge controls chalte rahenge.'
            : voice.micEnabled
              ? 'Mic live — bolo, agent sunega aur bolega. Typed sawal ka jawab text me aur (voice on ho to) awaaz me.'
              : voiceLive
                ? 'Mic off — room linked hai. Mic on dabao jab bolna ho; Agent listening alag control hai (mute = sirf awaaz band, typed chat chalti rahegi).'
                : 'Login ke baad room auto-link hota hai (mic off). Mic on = permission allow.'}
        </p>
        {voice.error ? (
          <p className="mt-1 max-w-md text-[11px] text-red-400" role="alert">
            Voice: {voice.error}
          </p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="icon-button"
          data-testid="assistant-agent-listening"
          data-muted={agentMuted ? 'true' : 'false'}
          title={listeningMutedTitle(persona)}
          onClick={() => emitUiCommandWithAck(agentMuted ? 'unmute' : 'mute')}
        >
          {agentMuted ? <EarOff size={14} className="text-command-danger" /> : <Ear size={14} />}
          <span className="ml-1 text-[10px] uppercase tracking-wide">
            {agentMuted ? 'Not listening' : 'Listening'}
          </span>
        </button>
        <div
          className="flex flex-col gap-1 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2"
          data-testid="voice-id-meter"
          data-state={meter.displayMode}
        >
          <div className="flex items-center gap-2">
            <ShieldCheck size={14} className={METER_TONE_CLASS[meter.tone]} />
            <span
              className="text-[10px] font-semibold uppercase tracking-wide text-command-text"
              data-testid="voice-id-meter-label"
            >
              {meter.label}
            </span>
            {meter.displayMode !== 'not_enrolled' ? (
              <span className="font-mono text-[11px] text-command-text">{meter.scorePercent}%</span>
            ) : null}
          </div>
          {meter.showEnrollLink ? (
            <button
              type="button"
              className="text-left text-[10px] text-command-accent hover:underline"
              data-testid="voice-id-enroll-link"
              onClick={onOpenVoiceEnroll}
            >
              Enroll
            </button>
          ) : (
            <span className="text-[10px] text-command-muted">
              Threshold {(meter.threshold * 100).toFixed(0)}%
            </span>
          )}
        </div>
        <button
          type="button"
          className="assistant-core__terminate relative static shrink-0"
          data-testid="terminate-session"
          title={shutdownAgentSupported ? 'Terminate session (restart agent)' : 'Agent update pending'}
          disabled={!shutdownAgentSupported}
          onClick={onTerminateSession}
        >
          <Power size={14} />
          Terminate
        </button>
      </div>
    </div>
  );
};

export default AssistantCore;
