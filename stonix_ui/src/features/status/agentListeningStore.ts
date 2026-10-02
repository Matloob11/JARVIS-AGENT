import { useSyncExternalStore } from 'react';
import { getSocket } from '@/shared/realtime/socket';
import { subscribeSocketEvent } from '@/shared/realtime/subscriptions';

function parseMuted(payload: unknown): boolean | null {
  if (typeof payload === 'boolean') return payload;
  if (payload && typeof payload === 'object' && 'muted' in payload) {
    return (payload as { muted?: boolean }).muted === true;
  }
  return null;
}

let agentMuted = false;
const listeners = new Set<() => void>();
let socketBound = false;

function emitChange() {
  for (const listener of listeners) {
    listener();
  }
}

function setAgentMuted(next: boolean) {
  if (agentMuted === next) return;
  agentMuted = next;
  emitChange();
}

function ensureSocketSubscriptions() {
  if (socketBound) return;
  socketBound = true;
  getSocket();

  subscribeSocketEvent('mute_update', payload => {
    const muted = parseMuted(payload);
    if (muted != null) setAgentMuted(muted);
  });

  subscribeSocketEvent('init_state', (state: unknown) => {
    if (!state || typeof state !== 'object') return;
    const row = state as { muted?: boolean; settings?: { muted?: boolean } };
    if (typeof row.muted === 'boolean') setAgentMuted(row.muted);
    else if (typeof row.settings?.muted === 'boolean') setAgentMuted(row.settings.muted);
  });

  subscribeSocketEvent('settings_sync', (payload: unknown) => {
    if (!payload || typeof payload !== 'object') return;
    const settings = (payload as { settings?: { muted?: boolean } }).settings;
    if (typeof settings?.muted === 'boolean') setAgentMuted(settings.muted);
  });
}

function subscribe(listener: () => void): () => void {
  ensureSocketSubscriptions();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): boolean {
  ensureSocketSubscriptions();
  return agentMuted;
}

/** Sync read for voice-room push-to-talk snapshots (not a React hook). */
export function readAgentListeningMuted(): boolean {
  return getSnapshot();
}

export function useAgentListeningMuted(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}
