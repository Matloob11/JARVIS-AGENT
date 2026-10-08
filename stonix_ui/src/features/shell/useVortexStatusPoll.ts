import { useEffect, useSyncExternalStore } from 'react';
import { bridgeUrl } from '@/shared/config/runtime';

export type VortexStatusSnapshot = {
  uiClients: number;
  laptopOnline: boolean | null;
  bridgeOk: boolean | null;
  dbOk: boolean | null;
  livekitOk: boolean | null;
  livekitLatencyMs: number | null;
  agentInRoom: boolean | null;
  brain: string | null;
  agentHost: string | null;
  agentVersion: string | null;
};

const DEFAULT: VortexStatusSnapshot = {
  uiClients: 1,
  laptopOnline: null,
  bridgeOk: null,
  dbOk: null,
  livekitOk: null,
  livekitLatencyMs: null,
  agentInRoom: null,
  brain: null,
  agentHost: null,
  agentVersion: null,
};

export function parseVortexStatus(body: Record<string, unknown>): VortexStatusSnapshot {
  const uiRaw = body.ui_clients;
  const uiClients = typeof uiRaw === 'number' && Number.isFinite(uiRaw) ? Math.max(0, uiRaw) : 1;
  let laptopOnline: boolean | null = null;
  const desktop = body.desktop;
  if (desktop && typeof desktop === 'object' && 'online' in desktop) {
    laptopOnline = (desktop as { online?: boolean }).online === true;
  }

  const bridgeBlock = body.bridge;
  const bridgeOk =
    bridgeBlock && typeof bridgeBlock === 'object'
      ? (bridgeBlock as { ok?: boolean }).ok === true
      : body.ok === true || body.bridge != null
        ? true
        : null;

  const dbBlock = body.db;
  const dbOk =
    dbBlock && typeof dbBlock === 'object' ? (dbBlock as { ok?: boolean }).ok === true : null;

  const lkBlock = body.livekit;
  let livekitOk: boolean | null = null;
  let livekitLatencyMs: number | null = null;
  let agentInRoom: boolean | null = null;
  if (lkBlock && typeof lkBlock === 'object') {
    const lk = lkBlock as Record<string, unknown>;
    livekitOk = lk.ok === true;
    if (typeof lk.latency_ms === 'number' && Number.isFinite(lk.latency_ms)) {
      livekitLatencyMs = lk.latency_ms;
    }
    if (typeof lk.agent_in_room === 'boolean') {
      agentInRoom = lk.agent_in_room;
    }
  }

  const brainRaw = body.brain;
  const brain = brainRaw != null ? String(brainRaw) : null;

  const agentBlock = body.agent;
  let agentHost: string | null = null;
  let agentVersion: string | null = null;
  if (agentBlock && typeof agentBlock === 'object') {
    const ag = agentBlock as Record<string, unknown>;
    if (ag.host != null) agentHost = String(ag.host);
    if (ag.version != null) agentVersion = String(ag.version);
  }

  return {
    uiClients,
    laptopOnline,
    bridgeOk,
    dbOk,
    livekitOk,
    livekitLatencyMs,
    agentInRoom,
    brain,
    agentHost,
    agentVersion,
  };
}

export async function pullVortexStatus(): Promise<VortexStatusSnapshot | null> {
  try {
    const res = await fetch(bridgeUrl('/status'), { credentials: 'include' });
    if (!res.ok) return null;
    const body = (await res.json()) as Record<string, unknown>;
    return parseVortexStatus(body);
  } catch {
    return null;
  }
}

let shared: VortexStatusSnapshot = DEFAULT;
const statusListeners = new Set<() => void>();
let pollTimer: number | null = null;
let pollUsers = 0;

function emitStatus() {
  for (const listener of statusListeners) listener();
}

async function refreshSharedStatus() {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
  const next = await pullVortexStatus();
  if (!next) return;
  shared = next;
  emitStatus();
}

function ensureStatusPoll() {
  if (pollTimer != null) return;
  void refreshSharedStatus();
  pollTimer = window.setInterval(() => void refreshSharedStatus(), 30_000);
  document.addEventListener('visibilitychange', refreshSharedStatus);
}

function releaseStatusPoll() {
  pollUsers = Math.max(0, pollUsers - 1);
  if (pollUsers > 0 || pollTimer == null) return;
  window.clearInterval(pollTimer);
  pollTimer = null;
  document.removeEventListener('visibilitychange', refreshSharedStatus);
}

function subscribeStatus(listener: () => void) {
  statusListeners.add(listener);
  return () => statusListeners.delete(listener);
}

function getSharedStatus() {
  return shared;
}

/** One poll for every chip and stats tile so the device count cannot drift. */
export function useVortexStatusPoll(enabled: boolean): VortexStatusSnapshot {
  const snapshot = useSyncExternalStore(subscribeStatus, getSharedStatus, getSharedStatus);

  useEffect(() => {
    if (!enabled) return undefined;
    pollUsers += 1;
    ensureStatusPoll();
    return () => releaseStatusPoll();
  }, [enabled]);

  return enabled ? snapshot : DEFAULT;
}

export function connectedDeviceCount(
  uiClients: number,
  agentOnline: boolean,
  laptopOnline: boolean | null,
): number {
  const otherUi = Math.max(0, uiClients - 1);
  let n = 1 + otherUi;
  if (agentOnline) n += 1;
  if (laptopOnline === true) n += 1;
  return n;
}
