import { getPersona, type PersonaKey } from '@/shared/persona';

export type ActivityFeedRow = {
  id: string;
  kind: string;
  level: string;
  title: string;
  subtitle: string;
  ts: number;
};

const rows: ActivityFeedRow[] = [];
const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) {
    listener();
  }
}

export function subscribeActivityFeed(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getActivityFeedRows(): ActivityFeedRow[] {
  return rows;
}

export function pushPersonaFeedRow(persona: PersonaKey, voice: string) {
  const copy = getPersona(persona);
  rows.unshift({
    id: `persona-${Date.now()}`,
    kind: 'persona',
    level: 'info',
    title: `${copy.name} is answering`,
    subtitle: `Voice ${voice}`,
    ts: Date.now(),
  });
  if (rows.length > 200) rows.length = 200;
  notify();
}
