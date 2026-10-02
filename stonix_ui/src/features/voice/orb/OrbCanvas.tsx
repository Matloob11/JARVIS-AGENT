import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { subscribeE2EVoiceChange } from '../e2eVoiceOverride';
import { getPersona, type PersonaKey } from '@/shared/persona';
import { Icon } from '@/shared/ui/icons/Icon';
import type { IconName } from '@/shared/ui/icons/Sprite';
import { useLayout } from '@/features/shell/useLayout';
import { OrbRenderer, type VoiceState } from './orbRenderer';
import { deriveOrbVoiceState } from './deriveOrbVoiceState';
import {
  getPersonaSwitchBridge,
  setPersonaSwitchBridge,
} from '@/features/overview/personaSwitchBridge';

type OrbCanvasProps = {
  persona: PersonaKey;
  statusLine: string;
  voiceUnavailable: boolean;
  roomState: import('../useVoiceRoom').VoiceRoomState;
  duplicateBlocked: boolean;
  linkedElsewhere: boolean;
  agentPresent: boolean;
  agentMissing: boolean;
  agentRestarting: boolean;
  agentOnline: boolean;
  playbackBlocked: boolean;
  agentState: import('../useVoiceRoom').AgentState;
  agentListeningMuted: boolean;
  micEnabled: boolean;
  pushToTalkActive?: boolean;
  isSpeaking: boolean;
  isThinking: boolean;
  isUserSpeaking: boolean;
  pageActive: boolean;
  waveCanvasRef: RefObject<HTMLCanvasElement | null>;
};

const GLYPH: Partial<
  Record<VoiceState, { icon: IconName; tone?: 'warn' | 'err' }>
> = {
  offline: { icon: 'alert', tone: 'warn' },
  'agent-absent': { icon: 'user-off' },
  'audio-blocked': { icon: 'speaker-off', tone: 'warn' },
  muted: { icon: 'ear-off' },
};

const OrbCanvas = ({
  persona,
  statusLine,
  voiceUnavailable,
  roomState,
  duplicateBlocked,
  linkedElsewhere,
  agentPresent,
  agentMissing,
  agentRestarting,
  agentOnline,
  playbackBlocked,
  agentState,
  agentListeningMuted,
  micEnabled,
  pushToTalkActive = false,
  isSpeaking,
  isThinking,
  isUserSpeaking,
  pageActive,
  waveCanvasRef,
}: OrbCanvasProps) => {
  const layout = useLayout();
  const hostRef = useRef<HTMLDivElement>(null);
  const orbRef = useRef<HTMLCanvasElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<OrbRenderer | null>(null);
  const [e2eVoiceTick, setE2eVoiceTick] = useState(0);

  useEffect(() => {
    if (!__E2E_HOOKS__) return undefined;
    return subscribeE2EVoiceChange(() => setE2eVoiceTick(t => t + 1));
  }, []);

  const voiceState = useMemo(
    () =>
      deriveOrbVoiceState({
        voiceUnavailable,
        roomState,
        duplicateBlocked,
        linkedElsewhere,
        agentPresent,
        agentMissing,
        agentRestarting,
        agentOnline,
        playbackBlocked,
        agentState,
        agentListeningMuted,
        micEnabled,
        pushToTalkActive,
        isSpeaking,
        isThinking,
        isUserSpeaking,
      }),
    [
      voiceUnavailable,
      roomState,
      duplicateBlocked,
      linkedElsewhere,
      agentPresent,
      agentMissing,
      agentRestarting,
      agentOnline,
      playbackBlocked,
      agentState,
      agentListeningMuted,
      micEnabled,
      pushToTalkActive,
      isSpeaking,
      isThinking,
      isUserSpeaking,
      e2eVoiceTick,
    ],
  );

  const voiceStateLive = useRef(voiceState);
  const personaLive = useRef(persona);
  voiceStateLive.current = voiceState;
  personaLive.current = persona;

  const copy = getPersona(persona);
  const orbLabel = `${copy.name} voice orb: ${statusLine}`;
  const glyph = GLYPH[voiceState];

  useEffect(() => {
    setPersonaSwitchBridge({
      ...getPersonaSwitchBridge(),
      snapOrbPalette: () => rendererRef.current?.snapPalette(),
    });
    return () => {
      const { snapOrbPalette: _drop, ...rest } = getPersonaSwitchBridge();
      setPersonaSwitchBridge(rest);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let orbRo: ResizeObserver | null = null;
    let waveRo: ResizeObserver | null = null;
    let io: IntersectionObserver | null = null;
    let renderer: OrbRenderer | null = null;
    let mountRaf = 0;
    let dropMq: (() => void) | null = null;

    const onVis = () => {
      if (!renderer) return;
      if (__E2E_HOOKS__) {
        renderer.setVisible(true);
        return;
      }
      renderer.setVisible(pageActive && !document.hidden);
    };

    const mount = () => {
      if (cancelled) return;
      const orb = orbRef.current;
      const wave = waveCanvasRef.current;
      const ring = ringRef.current;
      const host = hostRef.current;
      if (!orb || !wave || !ring || !host) {
        mountRaf = requestAnimationFrame(mount);
        return;
      }

      const phone = layout === 'ph';
      renderer = new OrbRenderer(orb, wave, ring, { phone });
      rendererRef.current = renderer;
      if (__E2E_HOOKS__) {
        renderer.setVisible(true);
      }
      renderer.setState(voiceStateLive.current, personaLive.current);

      orbRo = new ResizeObserver(entries => {
        for (const e of entries) {
          const w = e.contentRect.width;
          if (w > 0) renderer!.resizeOrb(w);
        }
      });
      orbRo.observe(host);

      waveRo = new ResizeObserver(entries => {
        for (const e of entries) {
          const { width, height } = e.contentRect;
          if (width > 0) renderer!.resizeWave(width, height > 0 ? height : 40);
        }
      });
      const waveBox = wave.parentElement;
      if (waveBox) waveRo.observe(waveBox);

      io = new IntersectionObserver(
        entries => {
          if (!renderer) return;
          if (__E2E_HOOKS__) {
            renderer.setVisible(true);
            return;
          }
          renderer.setVisible(entries.some(x => x.isIntersecting));
        },
        { threshold: 0 },
      );
      io.observe(orb);

      document.addEventListener('visibilitychange', onVis);

      const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
      const onMotion = () => renderer!.setReducedMotion(mq.matches);
      onMotion();
      mq.addEventListener('change', onMotion);
      dropMq = () => mq.removeEventListener('change', onMotion);
    };

    mount();

    return () => {
      cancelled = true;
      if (mountRaf) cancelAnimationFrame(mountRaf);
      dropMq?.();
      orbRo?.disconnect();
      waveRo?.disconnect();
      io?.disconnect();
      document.removeEventListener('visibilitychange', onVis);
      renderer?.destroy();
      rendererRef.current = null;
    };
  }, [layout, pageActive, waveCanvasRef]);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    renderer.setState(voiceState, persona);
  }, [voiceState, persona]);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    renderer.setVisible(pageActive && (__E2E_HOOKS__ || !document.hidden));
  }, [pageActive]);

  return (
    <div ref={hostRef} className="neon-orb-host">
      <canvas
        ref={orbRef}
        data-testid="speaker-orb"
        role="img"
        aria-label={orbLabel}
      />
      <div
        ref={ringRef}
        className="overview-hero__user-ring"
        data-testid="user-speaking-indicator"
        aria-hidden
        hidden
      />
      {glyph ? (
        <div className="neon-orb-glyph" data-tone={glyph.tone} aria-hidden>
          <Icon name={glyph.icon} />
        </div>
      ) : null}
    </div>
  );
};

export default OrbCanvas;
