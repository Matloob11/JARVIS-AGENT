import { toEpochMs } from '@/shared/contracts/events';
import { formatRelativeAgo } from '@/shared/time';
import { getSocket } from '@/shared/realtime/socket';
import { subscribeSocketEvent } from '@/shared/realtime/subscriptions';

export type DesktopLinkState = 'online' | 'stale' | 'offline' | 'locked' | 'panic';
export type DesktopControlMode = 'off' | 'read' | 'full';

export type DesktopStatusSnapshot = {
  seen: boolean;
  online: boolean;
  state: DesktopLinkState | null;
  mode: DesktopControlMode | null;
  locked: boolean;
  panic: boolean;
  voiceActive: boolean;
  nodeId: string | null;
  version: string | null;
  gitSha: string | null;
  manifestOk: boolean | null;
  sinceMs: number | null;
  lastSeenMs: number | null;
  rttMsP50: number | null;
  busyTool: string | null;
  brain: string | null;
};

const EMPTY: DesktopStatusSnapshot = {
  seen: false,
  online: false,
  state: null,
  mode: null,
  locked: false,
  panic: false,
  voiceActive: false,
  nodeId: null,
  version: null,
  gitSha: null,
  manifestOk: null,
  sinceMs: null,
  lastSeenMs: null,
  rttMsP50: null,
  busyTool: null,
  brain: null,
};

let snapshot: DesktopStatusSnapshot = { ...EMPTY };
const listeners = new Set<() => void>();
let socketBound = false;

function notify() {
  for (const listener of listeners) {
    listener();
  }
}

function parseMode(raw: unknown): DesktopControlMode | null {
  const s = String(raw ?? '').toLowerCase();
  if (s === 'off' || s === 'read' || s === 'full') return s;
  return null;
}

function parseState(raw: unknown): DesktopLinkState | null {
  const s = String(raw ?? '').toLowerCase();
  if (s === 'online' || s === 'stale' || s === 'offline' || s === 'locked' || s === 'panic') {
    return s;
  }
  return null;
}

export function normalizeDesktopStatus(raw: unknown): DesktopStatusSnapshot {
  if (!raw || typeof raw !== 'object') {
    return { ...EMPTY };
  }
  const row = raw as Record<string, unknown>;
  const state = parseState(row.state);
  const mode = parseMode(row.mode);
  const locked = row.locked === true;
  const panic = state === 'panic' || row.panic === true;
  const online = row.online === true;
  const lastSeenMs = toEpochMs(row.last_seen ?? row.lastSeen) ?? null;
  const sinceMs = toEpochMs(row.since) ?? null;
  const rtt =
    typeof row.rtt_ms_p50 === 'number' && Number.isFinite(row.rtt_ms_p50)
      ? Math.round(row.rtt_ms_p50)
      : null;

  return {
    seen: true,
    online,
    state,
    mode,
    locked,
    panic,
    voiceActive: row.voice_active === true,
    nodeId: row.node_id != null ? String(row.node_id) : null,
    version: row.version != null ? String(row.version) : null,
    gitSha: row.git_sha != null ? String(row.git_sha) : null,
    manifestOk: typeof row.manifest_ok === 'boolean' ? row.manifest_ok : null,
    sinceMs,
    lastSeenMs,
    rttMsP50: rtt,
    busyTool: row.busy_tool != null ? String(row.busy_tool) : null,
    brain: row.brain != null ? String(row.brain) : null,
  };
}

function applyDesktopStatus(raw: unknown) {
  const next = normalizeDesktopStatus(raw);
  if (!next.seen) return;
  snapshot = next;
  notify();
}

export function ensureDesktopStatusSubscriptions(): void {
  if (socketBound) return;
  socketBound = true;
  getSocket();

  subscribeSocketEvent('init_state', (state: unknown) => {
    if (!state || typeof state !== 'object') return;
    const desktop = (state as { desktop_status?: unknown }).desktop_status;
    if (desktop !== undefined) applyDesktopStatus(desktop);
  });

  subscribeSocketEvent('desktop_status', (data: unknown) => {
    applyDesktopStatus(data);
  });
}

export function subscribeDesktopStatus(onStoreChange: () => void): () => void {
  ensureDesktopStatusSubscriptions();
  listeners.add(onStoreChange);
  return () => listeners.delete(onStoreChange);
}

export function getDesktopStatusSnapshot(): DesktopStatusSnapshot {
  return snapshot;
}

export function formatDesktopLastSeen(lastSeenMs: number | null, nowMs: number): string {
  if (lastSeenMs == null || !Number.isFinite(lastSeenMs)) return 'never';
  const sec = Math.max(0, (nowMs - lastSeenMs) / 1000);
  return formatRelativeAgo(sec);
}

export function formatDesktopMode(mode: DesktopControlMode | null): string {
  if (mode === 'full') return 'Full';
  if (mode === 'read') return 'Read-only';
  if (mode === 'off') return 'Off';
  return '—';
}
