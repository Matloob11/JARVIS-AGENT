import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { toast } from '@/toast';
import { useSettings, type Persona } from '@/features/settings/useSettings';
import { pushPersonaFeedRow } from '@/features/activity/feedStore';
import { getPersona, type PersonaKey } from '@/shared/persona';
import { getPersonaSwitchBridge } from './personaSwitchBridge';

function personaRadiogroupTitle(active: PersonaKey): string {
  const self = getPersona(active);
  const other = getPersona(active === 'jarvis' ? 'anna' : 'jarvis');
  return `${self.name} answers. Voice ${self.voice}. Switch to ${other.name}: Voice ${other.voice}.`;
}

export function usePersonaSwitch() {
  const settings = useSettings();
  const persona = settings.persona;
  const pending = settings.isPersonaPending;

  const [focusIndex, setFocusIndex] = useState(() => (persona === 'anna' ? 1 : 0));
  const requestedRef = useRef<Persona | null>(null);
  const wasPendingRef = useRef(false);
  const revertedRef = useRef(false);

  useEffect(() => {
    setFocusIndex(persona === 'anna' ? 1 : 0);
  }, [persona]);

  useEffect(() => {
    if (pending) {
      wasPendingRef.current = true;
      revertedRef.current = false;
      return;
    }
    if (!wasPendingRef.current || !requestedRef.current) return;

    const requested = requestedRef.current;
    requestedRef.current = null;
    wasPendingRef.current = false;

    if (settings.persona === requested) {
      const copy = getPersona(requested);
      toast.success(`${copy.name} is answering. Voice ${copy.voice}.`);
      getPersonaSwitchBridge().appendChatSystemLine?.(`Switched to ${copy.name}`);
      pushPersonaFeedRow(requested, copy.voice);
      return;
    }

    if (!revertedRef.current) {
      toast('JARVIS hi jawab de raha hai.');
      revertedRef.current = true;
    }
  }, [pending, settings.persona]);

  const selectPersona = useCallback(
    (next: Persona) => {
      if (next === settings.persona || settings.isPersonaPending) return;
      requestedRef.current = next;
      settings.changePersona(next);
    },
    [settings],
  );

  const onRadioKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      const { key } = event;
      if (key === 'ArrowRight' || key === 'ArrowDown') {
        event.preventDefault();
        selectPersona('anna');
        setFocusIndex(1);
        return;
      }
      if (key === 'ArrowLeft' || key === 'ArrowUp') {
        event.preventDefault();
        selectPersona('jarvis');
        setFocusIndex(0);
        return;
      }
      if (key === ' ' || key === 'Enter') {
        event.preventDefault();
        const target = event.target as HTMLElement | null;
        const radio = target?.closest('[role="radio"]');
        const label = radio?.getAttribute('data-persona-radio');
        if (label === 'jarvis' || label === 'anna') {
          selectPersona(label);
        }
      }
    },
    [selectPersona],
  );

  return {
    persona,
    pending,
    focusIndex,
    radiogroupTitle: personaRadiogroupTitle(persona),
    selectPersona,
    onRadioKeyDown,
  };
}
