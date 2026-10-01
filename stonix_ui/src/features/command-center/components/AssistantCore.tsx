import { motion } from 'framer-motion';
import { Activity, Ear, EarOff, Mic, MicOff, Power, ShieldCheck } from 'lucide-react';
import { personaAccent } from '@/shared/theme/commandTheme';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useSettings } from '@/features/settings/useSettings';
import { deriveVoiceMeterView, type VoiceMatchLive } from '@/features/voice/voiceMeter';
import { emitUiCommandWithAck, useAgentListeningMuted } from '@/features/status/AgentControls';

interface AssistantCoreProps {
  persona: 'jarvis' | 'anna';
  isConnected: boolean;
  isSpeaking: boolean;
  isThinking: boolean;
  voiceMatchLive: VoiceMatchLive;
  onOpenVoiceEnroll: () => void;
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
  voiceMatchLive,
  onOpenVoiceEnroll,
}: AssistantCoreProps) => {
  const accent = personaAccent(persona);
  const settings = useSettings();
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
      <div className="assistant-core__grid" />
      <motion.div
        className="assistant-core__halo"
        animate={{ scale: isThinking || isSpeaking || voiceLive ? [1, 1.05, 1] : 1, opacity: isConnected ? 1 : 0.4 }}
        transition={{ duration: 2.8, repeat: Infinity }}
      />

      <div className="assistant-core__orb-wrap">
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
          animate={{ scale: isSpeaking || voiceLive ? [1, 1.035, 1] : 1 }}
          transition={{ duration: 1.4, repeat: isSpeaking || voiceLive ? Infinity : 0 }}
        >
          <span className={`assistant-core__pulse ${accent.bg}`} />
          <span className="assistant-core__label">{persona.toUpperCase()}</span>
          <span className="assistant-core__sublabel" data-testid="voice-status">
            {stateLabel}
          </span>
        </motion.div>
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

      <button className="assistant-core__terminate" title="Terminate session">
        <Power size={14} />
        Terminate
      </button>
    </div>
  );
};

export default AssistantCore;
