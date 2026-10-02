import { useSyncExternalStore } from 'react';
import { toEpochMs } from '@/shared/contracts/events';
import { getSocket } from '@/shared/realtime/socket';
import { subscribeSocketEvent } from '@/shared/realtime/subscriptions';
import type { AgentState } from '@/features/voice/useVoiceRoom';

export interface AgentStatusSnapshot {
  online: boolean;
  voiceReady: boolean;
  paused: boolean;
  restarting: boolean;
  sinceMs?: number;
  lastSeenMs?: number;
  session?: string;
  mode?: string;
}

const EMPTY: AgentStatusSnapshot = {
  online: false,
  voiceReady: false,
  paused: false,
  restarting: false,
};

export type AgentActivitySnapshot = {
  status: AgentStatusSnapshot;
  legacyStatus: string;
  speaking: boolean;
  thinking: boolean;
  agentStatusSeen: boolean;
};

type StoreSnapshot = AgentActivitySnapshot;

let snapshot: StoreSnapshot = {
  status: { ...EMPTY },
  legacyStatus: 'idle',
  speaking: false,
  thinking: false,
  agentStatusSeen: false,
};

const listeners = new Set<() => void>();
let socketBound = false;

function emitChange() {
  for (const listener of listeners) {
    listener();
  }
}

function setPartial(partial: Partial<StoreSnapshot>) {
  snapshot = { ...snapshot, ...partial };
  emitChange();
}

export function normalizeAgentStatus(raw: unknown): AgentStatusSnapshot {
  if (!raw || typeof raw !== 'object') return { ...EMPTY };
  const data = raw as Record<string, unknown>;
  const voiceReady =
    data.voice_ready === true ||
    data.in_room === true ||
    data.voiceReady === true;
  const lastRaw = data.last_heartbeat ?? data.last_seen ?? data.lastSeen;
  return {
    online: data.online === true,
    voiceReady,
    paused: data.paused === true,
    restarting: data.restarting === true,
    sinceMs: toEpochMs(data.since),
    lastSeenMs: toEpochMs(lastRaw),
    session: data.session != null ? String(data.session) : undefined,
    mode: data.mode != null ? String(data.mode) : undefined,
  };
}

function ensureSocketSubscriptions() {
  if (socketBound) return;
  socketBound = true;
  getSocket();

  subscribeSocketEvent('agent_status', payload => {
    setPartial({
      status: { ...snapshot.status, ...normalizeAgentStatus(payload) },
      agentStatusSeen: true,
    });
  });

  subscribeSocketEvent('init_state', (state: unknown) => {
    if (!state || typeof state !== 'object') return;
    const agentStatus = (state as { agent_status?: unknown }).agent_status;
    if (agentStatus !== undefined) {
      setPartial({
        status: normalizeAgentStatus(agentStatus),
        agentStatusSeen: true,
      });
    }
  });

  subscribeSocketEvent('status_update', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    const next: Partial<StoreSnapshot> = {};
    if (typeof row.status === 'string') next.legacyStatus = row.status;
    if (typeof row.speaking === 'boolean') next.speaking = row.speaking;
    if (typeof row.thinking === 'boolean') next.thinking = row.thinking;
    if (Object.keys(next).length) setPartial(next);
  });

  subscribeSocketEvent('status_change', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = data as { speaking?: boolean; thinking?: boolean };
    const next: Partial<StoreSnapshot> = {};
    if (typeof row.speaking === 'boolean') next.speaking = row.speaking;
    if (typeof row.thinking === 'boolean') next.thinking = row.thinking;
    if (Object.keys(next).length) setPartial(next);
  });
}

function subscribe(listener: () => void): () => void {
  ensureSocketSubscriptions();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): StoreSnapshot {
  ensureSocketSubscriptions();
  return snapshot;
}

export function useAgentStatusStore(): StoreSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

export function labelFromAgentState(state: AgentState): string | null {
  if (state === 'listening' || state === 'thinking' || state === 'speaking' || state === 'initializing') {
    return state;
  }
  return null;
}

export function formatLastSeen(lastSeenMs: number | undefined, nowMs: number): string {
  if (lastSeenMs == null || !Number.isFinite(lastSeenMs)) return 'last seen unknown';
  const agoSec = Math.max(0, Math.floor((nowMs - lastSeenMs) / 1000));
  return `last seen ${agoSec}s ago`;
}
