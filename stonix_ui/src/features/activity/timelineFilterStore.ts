import { useSyncExternalStore } from 'react';
import type { ActivityFeedRow } from './feedStore';

export type TimelineFilter =
  | 'all'
  | 'system'
  | 'voice'
  | 'device'
  | 'approval'
  | 'tool'
  | 'reminder'
  | 'media';

export const TIMELINE_FILTERS: { id: TimelineFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'system', label: 'System' },
  { id: 'voice', label: 'Voice' },
  { id: 'device', label: 'Devices' },
  { id: 'approval', label: 'Approvals' },
  { id: 'tool', label: 'Tool runs' },
  { id: 'reminder', label: 'Reminders' },
  { id: 'media', label: 'Media' },
];

const KIND_GROUP: Record<string, TimelineFilter> = {
  system: 'system',
  sync: 'system',
  persona: 'system',
  memory: 'system',
  alert: 'system',
  log: 'system',
  voice: 'voice',
  device: 'device',
  approval: 'approval',
  tool: 'tool',
  reminder: 'reminder',
  media: 'media',
};

let filter: TimelineFilter = 'all';
const listeners = new Set<() => void>();

function notify() {
  for (const listener of listeners) {
    listener();
  }
}

export function getTimelineFilter(): TimelineFilter {
  return filter;
}

export function setTimelineFilter(next: TimelineFilter): void {
  if (filter === next) return;
  filter = next;
  notify();
}

export function subscribeTimelineFilter(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTimelineFilter(): TimelineFilter {
  return useSyncExternalStore(subscribeTimelineFilter, getTimelineFilter, getTimelineFilter);
}

export function rowMatchesTimelineFilter(row: ActivityFeedRow, active: TimelineFilter): boolean {
  if (active === 'all') return true;
  const group = KIND_GROUP[row.kind] ?? 'system';
  return group === active;
}
