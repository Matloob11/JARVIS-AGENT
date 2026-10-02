import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Activity, Ear, EarOff, Mic, MicOff, Power, ShieldCheck } from 'lucide-react';
import { personaAccent } from '@/shared/theme/commandTheme';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useSettings } from '@/features/settings/useSettings';
import { deriveVoiceMeterView, type VoiceMatchLive } from '@/features/voice/voiceMeter';
import { audioEngine, bandsToDisplayBytes, DISPLAY_BAR_COUNT } from '@/features/voice/audioEngine';
import { emitUiCommandWithAck, useAgentListeningMuted } from '@/features/status/AgentControls';
import { useAgentStatus } from '@/features/status/useAgentStatus';

interface AssistantCoreProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  isSpeaking: boolean;
  isThinking: boolean;
  isUserSpeaking: boolean;
  agentAudioActive: boolean;
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
  isSpeaking,
  isThinking,
  isUserSpeaking,
  agentAudioActive,
  voiceMatchLive,
  onOpenVoiceEnroll,
  sessionRestartBanner,
  onSessionRestartBannerChange,
}: AssistantCoreProps) => {
  const accent = personaAccent(persona);
  const settings = useSettings();
  const agentStatus = useAgentStatus();
  const [shutdownAgentSupported, setShutdownAgentSupported] = useState(true);
  const spectralRef = useRef<HTMLDivElement>(null);
  const orbWrapRef = useRef<HTMLDivElement>(null);
  const orbScaleRef = useRef(1);
  const meter = deriveVoiceMeterView(
    {
      voiceId: settings.voiceId,
      voiceIdEnrolled: settings.voiceIdEnrolled,
      voiceIdThreshold: settings.voiceIdThreshold,
      enforcedVoiceId: settings.enforcedVoiceId,
    },
    voiceMatchLive,
  );
  const voice = useLiveKit();
  const agentMuted = useAgentListeningMuted();
  const voiceLive = voice.roomState === 'connected';
  const voiceConnecting = voice.roomState === 'connecting' || voice.roomState === 'reconnecting';
  const agentLabel = voice.agentState ? voice.agentState : voiceLive ? 'linked' : 'offline';
  const showUserSpeaking = isUserSpeaking && voice.micEnabled;

  useEffect(() => {
    const root = spectralRef.current;
    if (!root) return undefined;
    const barEls = Array.from({ length: DISPLAY_BAR_COUNT }, () => {
      const span = document.createElement('span');
      span.className = 'assistant-core__spectral-bar';
      span.style.height = '4%';
      root.appendChild(span);
      return span;
    });

    let raf = 0;
    const tick = () => {
      const bytes = bandsToDisplayBytes(audioEngine.readAgent().bands);
      for (let i = 0; i < DISPLAY_BAR_COUNT; i++) {
        const heightPct = Math.max(4, Math.round((bytes[i] / 255) * 100));
        barEls[i].style.height = `${heightPct}%`;
      }
      const level = audioEngine.readAgent().level;
      orbScaleRef.current = 1 + Math.min(level, 1) * 0.08;
      if (orbWrapRef.current) {
        orbWrapRef.current.style.setProperty('--orb-scale', String(orbScaleRef.current));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      barEls.forEach(el => el.remove());
    };
  }, []);

  const orbPulse = agentAudioActive;
  const orbScale = orbScaleRef.current;

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
          : isSpeaking
            ? 'Speaking'
            : isConnected
              ? 'System active'
              : 'Offline';

  return (
    <div className="assistant-core">
      {sessionRestartBanner ? (
        <div
          className="assistant-core__restart-banner"
          data-testid="agent-session-restart-banner"
          role="status"
        >
          Agent session restart ho raha hai
        </div>
      ) : null}
      <div className="assistant-core__grid" />
      <motion.div
        className="assistant-core__halo"
        animate={{
          scale: isThinking || orbPulse ? [1, 1.05, 1] : 1,
          opacity: isConnected ? 1 : 0.4,
        }}
        transition={{ duration: 2.8, repeat: Infinity }}
      />

      <div className="assistant-core__viz-wrap">
        <div
          ref={spectralRef}
          className="assistant-core__spectral"
          data-testid="agent-audio-visualizer"
          aria-hidden={!agentAudioActive}
        />

        <div className="assistant-core__orb-wrap" ref={orbWrapRef}>
          {showUserSpeaking ? (
            <motion.div
              className="assistant-core__user-speaking-ring"
              data-testid="user-speaking-indicator"
              animate={{ scale: [1, 1.06, 1], opacity: [0.85, 1, 0.85] }}
              transition={{ duration: 1.2, repeat: Infinity }}
            />
          ) : null}
          <motion.div
            className="assistant-core__ring assistant-core__ring--outer"
            animate={{ rotate: 360 }}
            transition={{ duration: 42, repeat: Infinity, ease: 'linear' }}
          />
          <motion.div
            className="assistant-core__ring assistant-core__ring--inner"
            animate={{ rotate: -360 }}
            transition={{ duration: 34, repeat: Infinity, ease: 'linear' }}
          />
          <motion.div
            className={`assistant-core__orb ${accent.shadow}`}
            animate={{ scale: orbPulse ? [orbScale, orbScale * 1.04, orbScale] : orbScale }}
            transition={{ duration: 1.4, repeat: orbPulse ? Infinity : 0 }}
          >
            <span className={`assistant-core__pulse ${accent.bg}`} />
            <span className="assistant-core__label">{persona.toUpperCase()}</span>
            <span className="assistant-core__sublabel" data-testid="voice-status">
              {stateLabel}
            </span>
          </motion.div>
        </div>

        {showUserSpeaking ? (
          <p className="assistant-core__user-speaking-label" data-testid="user-speaking-label">
            You are speaking
          </p>
        ) : null}
      </div>

      <div className="assistant-core__footer">
        <div>
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
          {voice.error && (
            <p className="mt-1 max-w-md text-[11px] text-red-400" role="alert">
              Voice: {voice.error}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="icon-button"
            data-testid="voice-toggle"
            title={voice.micEnabled ? 'Mic off (browser publish)' : 'Mic on (browser publish)'}
            onClick={() => void voice.toggleMic()}
            disabled={voiceConnecting || voice.voiceUnavailable}
          >
            {voice.micEnabled ? <Mic size={14} /> : <MicOff size={14} />}
            <span className="ml-1 text-[10px] uppercase tracking-wide">
              {voiceConnecting ? 'Joining…' : voice.micEnabled ? 'Mic on' : 'Mic off'}
            </span>
          </button>
          <button
            type="button"
            className="icon-button"
            data-testid="assistant-agent-listening"
            data-muted={agentMuted ? 'true' : 'false'}
            title="JARVIS mic se nahi sunega; typed chat chalti rahegi"
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
        </div>
      </div>

      <button
        type="button"
        className="assistant-core__terminate"
        data-testid="terminate-session"
        title={shutdownAgentSupported ? 'Terminate session (restart agent)' : 'Agent update pending'}
        disabled={!shutdownAgentSupported}
        onClick={onTerminateSession}
      >
        <Power size={14} />
        Terminate
      </button>
    </div>
  );
};

export default AssistantCore;
