import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Loader2, Mic, MicOff, Zap } from 'lucide-react';
import { getPersona, type PersonaKey } from '@/shared/persona';
import { StatusShape } from '@/shared/ui/StatusShape';
import { useLiveKit } from '@/features/voice/LiveKitProvider';
import { useVoiceUi } from '@/features/voice/voiceState';
import { emitUiCommandWithAck, HeroStopButton } from '@/features/status/AgentControls';
import { useAgentListeningMuted } from '@/features/status/AgentControls';
import { useSwarmMode } from '@/features/panel/useSwarmMode';
import { usePersonaSwitch } from './usePersonaSwitch';
import { prefersReducedMotion } from './personaSwitchMotion';

type HeroCardProps = {
  persona: PersonaKey;
  isSpeaking: boolean;
  isThinking: boolean;
  isUserSpeaking: boolean;
  orbSlot?: ReactNode;
  waveformSlot?: ReactNode;
};

const HeroCard = ({
  persona,
  isSpeaking,
  isThinking,
  isUserSpeaking,
  orbSlot,
  waveformSlot,
}: HeroCardProps) => {
  const copy = getPersona(persona);
  const [displayName, setDisplayName] = useState(copy.name);
  const nameRef = useRef<HTMLHeadingElement>(null);
  const personaSwitch = usePersonaSwitch();

  useLayoutEffect(() => {
    if (prefersReducedMotion()) {
      setDisplayName(copy.name);
    }
  }, [copy.name]);

  useEffect(() => {
    const nextName = copy.name;
    if (nextName === displayName) return undefined;

    const nm = nameRef.current;
    if (!nm || prefersReducedMotion()) {
      setDisplayName(nextName);
      return undefined;
    }

    let cancelled = false;
    const out = nm.animate(
      [
        { transform: 'translateY(0)', opacity: 1 },
        { transform: 'translateY(-14px)', opacity: 0 },
      ],
      { duration: 220, easing: 'ease-in' },
    );
    out.onfinish = () => {
      if (cancelled) return;
      setDisplayName(nextName);
      requestAnimationFrame(() => {
        if (cancelled || !nameRef.current) return;
        nameRef.current.animate(
          [
            { transform: 'translateY(14px)', opacity: 0 },
            { transform: 'translateY(0)', opacity: 1 },
          ],
          { duration: 320, easing: 'cubic-bezier(.2,.8,.2,1)' },
        );
      });
    };

    return () => {
      cancelled = true;
      out.cancel();
    };
  }, [copy.name, displayName]);
  const voice = useLiveKit();
  const agentMuted = useAgentListeningMuted();
  const { swarmMode, toggleSwarmMode } = useSwarmMode();

  const ui = useVoiceUi(persona, { isSpeaking, isThinking, isUserSpeaking });
  const { status, buttons, helper, isHelper, showUserSpeaking } = ui;

  const onStartListening = () => {
    if (buttons.listenDisabled) return;
    if (!voice.micEnabled) {
      void voice.toggleMic();
    }
    if (agentMuted) {
      emitUiCommandWithAck('unmute');
    }
  };

  const onMicOff = () => {
    if (buttons.micOffDisabled) return;
    if (voice.micEnabled) {
      void voice.toggleMic();
    }
  };

  const inlineAction = (() => {
    switch (status.inlineAction) {
      case 'take-over':
        return (
          <button
            type="button"
            className="overview-hero__status-act"
            data-testid="voice-take-over"
            onClick={() => void voice.takeOver()}
          >
            Take over
          </button>
        );
      case 'wake':
        return (
          <button
            type="button"
            className="overview-hero__status-act"
            data-testid="voice-wake-agent"
            onClick={voice.wakeAgent}
          >
            Wake
          </button>
        );
      case 'enable-audio':
        return (
          <button
            type="button"
            className="overview-hero__status-act"
            data-testid="enable-audio"
            onClick={() => void voice.enablePlayback()}
          >
            Enable audio
          </button>
        );
      case 'stop':
        return (
          <span className="overview-hero__status-act-wrap">
            <HeroStopButton className="overview-hero__status-act" />
          </span>
        );
      default:
        return null;
    }
  })();

  return (
    <article className="overview-hero edge" data-testid="hero-card" data-voice={ui.state}>
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

      {showUserSpeaking ? (
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

      <h2
        ref={nameRef}
        className="overview-hero__name t"
        data-testid="hero-persona-name"
      >
        {displayName}
      </h2>
      <p className="overview-hero__role t">AI Assistant</p>
      <div className="overview-hero__rule t" aria-hidden />

      <div className="overview-hero__wave t" data-testid="agent-audio-visualizer">
        {waveformSlot ?? <div className="overview-hero__wave-placeholder" aria-hidden />}
      </div>

      <p
        className={`overview-hero__status t overview-hero__status-row${showUserSpeaking ? ' overview-hero__status--hidden' : ''}`}
        data-testid="voice-status"
        data-tone={status.tone}
      >
        {status.showSpinner ? (
          <Loader2 className="overview-hero__status-spin" size={16} aria-hidden />
        ) : status.shape ? (
          <StatusShape
            s={status.shape}
            label={status.line}
            hideLabel
            className="overview-hero__status-shape"
          />
        ) : null}
        <span className="overview-hero__status-text">{status.line}</span>
        {inlineAction}
      </p>
      {showUserSpeaking ? (
        <p
          className="overview-hero__status-user t"
          data-testid="user-speaking-label"
          aria-hidden={!showUserSpeaking}
        >
          ● You are speaking
        </p>
      ) : null}

      <div className="overview-hero__btns t" role="group" aria-label="Microphone">
        <button
          type="button"
          className={`overview-hero__btn overview-hero__btn--listen edge${buttons.listenPressed ? ' is-pressed' : ''}`}
          data-testid="voice-toggle"
          aria-pressed={buttons.listenPressed}
          disabled={buttons.listenDisabled}
          title={voice.micEnabled ? 'Mic off (browser publish)' : 'Mic on (browser publish)'}
          onClick={onStartListening}
        >
          <Mic className="overview-hero__btn-ic" size={17} aria-hidden />
          <span className="overview-hero__btn-lbl">{buttons.listenLabel}</span>
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
          className={`overview-hero__btn overview-hero__btn--mic-off edge${buttons.micOffPressed ? ' is-pressed' : ''}`}
          data-testid="hero-mic-off"
          aria-pressed={buttons.micOffPressed}
          disabled={buttons.micOffDisabled}
          title="Mic off (stop publishing)"
          onClick={onMicOff}
        >
          <MicOff className="overview-hero__btn-ic" size={16} aria-hidden />
          <span className="overview-hero__btn-lbl">Mic Off</span>
        </button>
      </div>

      <p
        className="overview-hero__quote t"
        data-testid="hero-helper"
        lang={isHelper ? 'ur-Latn' : undefined}
        data-helper={isHelper ? 'true' : undefined}
      >
        {helper}
      </p>
    </article>
  );
};

export default HeroCard;
