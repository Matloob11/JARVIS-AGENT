import { useEffect, useMemo, useRef } from 'react';
import { getPersona, type PersonaKey } from '@/shared/persona';
import { Icon } from '@/shared/ui/icons/Icon';
import type { IconName } from '@/shared/ui/icons/Sprite';
import { useLayout } from '@/features/shell/useLayout';
import { useHashRouter } from '@/features/shell/useHashRouter';
import { OrbRenderer, type VoiceState } from './orbRenderer';
import { deriveOrbVoiceState } from './deriveOrbVoiceState';

type OrbCanvasProps = {
  persona: PersonaKey;
  statusLine: string;
  voiceUnavailable: boolean;
  roomState: import('../useVoiceRoom').VoiceRoomState;
  agentState: import('../useVoiceRoom').AgentState;
  agentListeningMuted: boolean;
  micEnabled: boolean;
  isSpeaking: boolean;
  isThinking: boolean;
  isUserSpeaking: boolean;
  pageActive: boolean;
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
  agentState,
  agentListeningMuted,
  micEnabled,
  isSpeaking,
  isThinking,
  isUserSpeaking,
  pageActive,
}: OrbCanvasProps) => {
  const layout = useLayout();
  const route = useHashRouter(layout);
  const hostRef = useRef<HTMLDivElement>(null);
  const orbRef = useRef<HTMLCanvasElement>(null);
  const waveRef = useRef<HTMLCanvasElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<OrbRenderer | null>(null);

  const voiceState = useMemo(
    () =>
      deriveOrbVoiceState({
        voiceUnavailable,
        roomState,
        agentState,
        agentListeningMuted,
        micEnabled,
        isSpeaking,
        isThinking,
        isUserSpeaking,
      }),
    [
      voiceUnavailable,
      roomState,
      agentState,
      agentListeningMuted,
      micEnabled,
      isSpeaking,
      isThinking,
      isUserSpeaking,
    ],
  );

  const copy = getPersona(persona);
  const orbLabel = `${copy.name} voice orb: ${statusLine}`;
  const glyph = GLYPH[voiceState];

  useEffect(() => {
    const orb = orbRef.current;
    const wave = waveRef.current;
    const ring = ringRef.current;
    const host = hostRef.current;
    if (!orb || !wave || !ring || !host) return undefined;

    const phone = layout === 'ph';
    const renderer = new OrbRenderer(orb, wave, ring, { phone });
    rendererRef.current = renderer;

    const ro = new ResizeObserver(entries => {
      for (const e of entries) {
        const w = e.contentRect.width;
        if (w > 0) renderer.resize(w, 216, 40);
      }
    });
    ro.observe(host);

    const io = new IntersectionObserver(
      entries => {
        renderer.setVisible(entries.some(x => x.isIntersecting));
      },
      { threshold: 0 },
    );
    io.observe(orb);

    const onVis = () => renderer.setVisible(pageActive && !document.hidden);
    document.addEventListener('visibilitychange', onVis);

    const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    const onMotion = () => renderer.setReducedMotion(mq.matches);
    onMotion();
    mq.addEventListener('change', onMotion);

    return () => {
      ro.disconnect();
      io.disconnect();
      document.removeEventListener('visibilitychange', onVis);
      mq.removeEventListener('change', onMotion);
      renderer.destroy();
      rendererRef.current = null;
    };
  }, [layout, pageActive]);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    renderer.setState(voiceState, persona);
  }, [voiceState, persona]);

  useEffect(() => {
    const renderer = rendererRef.current;
    if (!renderer) return;
    const onOverview =
      route.page === 'overview' && (layout !== 'ph' || pageActive);
    renderer.setVisible(onOverview && !document.hidden);
  }, [route.page, layout, pageActive]);

  return (
    <div ref={hostRef} className="neon-orb-host">
      <canvas
        ref={orbRef}
        data-testid="speaker-orb"
        role="img"
        aria-label={orbLabel}
      />
      <canvas ref={waveRef} className="sr-only" aria-hidden width={216} height={40} />
      <div
        ref={ringRef}
        className="overview-hero__user-ring"
        data-testid="user-speaking-indicator"
        aria-hidden
        hidden
      />
      {glyph ? (
        <div
          className="neon-orb-glyph"
          data-tone={glyph.tone}
          aria-hidden
        >
          <Icon name={glyph.icon} />
        </div>
      ) : null}
    </div>
  );
};

export default OrbCanvas;
