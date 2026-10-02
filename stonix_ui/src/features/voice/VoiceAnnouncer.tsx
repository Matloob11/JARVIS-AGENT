import { useEffect, useRef } from 'react';
import type { PersonaKey } from '@/shared/persona';
import type { VoiceUiState } from '@/shared/persona';
import { voiceAnnouncementMessage } from './voiceState';

type VoiceAnnouncerProps = {
  persona: PersonaKey;
  state: VoiceUiState;
};

const VoiceAnnouncer = ({ persona, state }: VoiceAnnouncerProps) => {
  const prevRef = useRef<VoiceUiState | null>(null);
  const lastMsgRef = useRef('');
  const timerRef = useRef<number | null>(null);
  const regionRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const prev = prevRef.current;
    prevRef.current = state;
    const msg = voiceAnnouncementMessage(state, persona, prev);
    if (!msg || msg === lastMsgRef.current) return;

    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
    }

    timerRef.current = window.setTimeout(() => {
      const el = regionRef.current;
      if (!el) return;
      el.textContent = '';
      window.setTimeout(() => {
        el.textContent = msg;
        lastMsgRef.current = msg;
      }, 40);
    }, 1200);

    return () => {
      if (timerRef.current != null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, [persona, state]);

  return (
    <div
      id="sr"
      ref={regionRef}
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="vh"
    />
  );
};

export default VoiceAnnouncer;
