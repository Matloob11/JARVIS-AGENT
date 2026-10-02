import type { ReactNode } from 'react';
import { Mic, MicOff, Zap } from 'lucide-react';
import { QUOTE } from '@/shared/owner';
import { getPersona, type PersonaKey } from '@/shared/persona';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useAgentListeningMuted, emitUiCommandWithAck } from '@/features/status/AgentControls';
import { useSwarmMode } from '@/features/panel/useSwarmMode';
import { usePersonaSwitch } from './usePersonaSwitch';

type HeroCardProps = {
  persona: PersonaKey;
  orbSlot?: ReactNode;
  waveformSlot?: ReactNode;
  statusSlot?: ReactNode;
  isUserSpeaking?: boolean;
  stateLabel?: string;
};

const HeroCard = ({
  persona,
  orbSlot,
  waveformSlot,
  statusSlot,
  isUserSpeaking = false,
  stateLabel = 'Listening for your command',
}: HeroCardProps) => {
  const copy = getPersona(persona);
  const personaSwitch = usePersonaSwitch();
  const voice = useLiveKit();
  const agentMuted = useAgentListeningMuted();
  const { swarmMode, toggleSwarmMode } = useSwarmMode();

  const voiceConnecting =
    voice.roomState === 'connecting' || voice.roomState === 'reconnecting';
  const micPressed = voice.micEnabled && !agentMuted;

  const onStartListening = () => {
    if (voiceConnecting || voice.voiceUnavailable) return;
    if (!voice.micEnabled) {
      void voice.toggleMic();
    }
    if (agentMuted) {
      emitUiCommandWithAck('unmute');
    }
  };

  const onMicOff = () => {
    if (voiceConnecting || voice.voiceUnavailable) return;
    if (voice.micEnabled) {
      void voice.toggleMic();
    }
  };

  const listenLabel = voiceConnecting ? 'Joining…' : 'Start Listening';
  const listenDisabled = voiceConnecting || voice.voiceUnavailable;

  return (
    <article className="overview-hero edge" data-testid="hero-card">
      <div className="overview-hero__lights" aria-hidden />
      <svg className="overview-hero__flare" viewBox="0 0 830 474" aria-hidden>
        <defs>
          <linearGradient id="hero-flare-spark" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="var(--fl-spark)" />
          </linearGradient>
          <linearGradient id="hero-flare-arc" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--fl-arc)" />
          </linearGradient>
          <linearGradient id="hero-flare-arc2" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="var(--fl-arc2)" />
          </linearGradient>
        </defs>
        <circle cx="239.6" cy="203.7" r="2" fill="url(#hero-flare-spark)" />
        <path
          d="M 120 210 A 130 130 0 0 1 360 210"
          fill="none"
          stroke="url(#hero-flare-arc)"
          strokeWidth="1.2"
          opacity="0.85"
        />
        <path
          d="M 90 250 A 160 160 0 0 0 390 250"
          fill="none"
          stroke="url(#hero-flare-arc2)"
          strokeWidth="1"
          opacity="0.65"
        />
      </svg>

      <div className="overview-hero__orb-slot" data-testid="speaker-orb">
        {orbSlot ?? <div className="overview-hero__orb-placeholder" aria-hidden />}
      </div>

      {isUserSpeaking ? (
        <div
          className="overview-hero__user-ring"
          data-testid="user-speaking-indicator"
          aria-hidden
        />
      ) : null}

      <div
        className="overview-hero__eyebrow t"
        role="radiogroup"
        aria-label="Who answers"
        aria-busy={personaSwitch.pending ? 'true' : undefined}
        data-testid="chip-persona"
        title={personaSwitch.radiogroupTitle}
        tabIndex={0}
        onKeyDown={personaSwitch.onRadioKeyDown}
      >
        <button
          type="button"
          role="radio"
          aria-checked={personaSwitch.persona === 'jarvis'}
          tabIndex={personaSwitch.persona === 'jarvis' ? 0 : -1}
          data-persona-radio="jarvis"
          className={personaSwitch.persona === 'jarvis' ? 'is-on' : undefined}
          onClick={() => personaSwitch.selectPersona('jarvis')}
        >
          Jarvis
        </button>
        <span className="overview-hero__eyebrow-sep" aria-hidden>
          {' '}
          &amp;{' '}
        </span>
        <button
          type="button"
          role="radio"
          aria-checked={personaSwitch.persona === 'anna'}
          tabIndex={personaSwitch.persona === 'anna' ? 0 : -1}
          data-persona-radio="anna"
          className={personaSwitch.persona === 'anna' ? 'is-on' : undefined}
          onClick={() => personaSwitch.selectPersona('anna')}
        >
          Anna
        </button>
        {personaSwitch.pending ? (
          <span
            className="overview-hero__eyebrow-spin"
            data-testid="chip-persona-spinner"
            aria-hidden
          />
        ) : null}
      </div>

      <h2 className="overview-hero__name t" data-testid="hero-persona-name">
        {copy.name}
      </h2>
      <p className="overview-hero__role t">AI Assistant</p>
      <div className="overview-hero__rule t" aria-hidden />

      <div className="overview-hero__wave t" data-testid="agent-audio-visualizer">
        {waveformSlot ?? <div className="overview-hero__wave-placeholder" aria-hidden />}
      </div>

      <p
        className={`overview-hero__status t${isUserSpeaking ? ' overview-hero__status--hidden' : ''}`}
        data-testid="voice-status"
      >
        {statusSlot ?? stateLabel}
      </p>
      {isUserSpeaking ? (
        <p
          className="overview-hero__status-user t"
          data-testid="user-speaking-label"
          aria-hidden={!isUserSpeaking}
        >
          ● You are speaking
        </p>
      ) : null}

      <div className="overview-hero__btns t" role="group" aria-label="Microphone">
        <button
          type="button"
          className={`overview-hero__btn overview-hero__btn--listen edge${micPressed ? ' is-pressed' : ''}`}
          data-testid="voice-toggle"
          aria-pressed={micPressed}
          disabled={listenDisabled}
          title={voice.micEnabled ? 'Mic off (browser publish)' : 'Mic on (browser publish)'}
          onClick={onStartListening}
        >
          <Mic className="overview-hero__btn-ic" size={17} aria-hidden />
          <span className="overview-hero__btn-lbl">{listenLabel}</span>
        </button>
        <button
          type="button"
          className={`overview-hero__btn overview-hero__btn--task edge${swarmMode ? ' is-pressed' : ''}`}
          data-testid="swarm-mode-toggle"
          data-active={swarmMode ? 'true' : 'false'}
          aria-pressed={swarmMode}
          title={swarmMode ? 'Swarm on — Send runs swarm task' : 'Swarm off — normal chat'}
          onClick={() => toggleSwarmMode()}
        >
          <Zap className="overview-hero__btn-ic" size={16} fill="currentColor" aria-hidden />
          <span className="overview-hero__btn-lbl">Run Task</span>
        </button>
        <button
          type="button"
          className={`overview-hero__btn overview-hero__btn--mic-off edge${!voice.micEnabled ? ' is-pressed' : ''}`}
          data-testid="hero-mic-off"
          aria-pressed={!voice.micEnabled}
          disabled={listenDisabled}
          title="Mic off (stop publishing)"
          onClick={onMicOff}
        >
          <MicOff className="overview-hero__btn-ic" size={16} aria-hidden />
          <span className="overview-hero__btn-lbl">Mic Off</span>
        </button>
      </div>

      <p className="overview-hero__quote t" data-testid="hero-helper">
        {QUOTE}
      </p>
    </article>
  );
};

export default HeroCard;
