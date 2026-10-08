import { useSyncExternalStore } from 'react';
import { normalizeConfirm } from '@/shared/contracts/events';
import { getPersona, type PersonaKey } from '@/shared/persona';
import { normalizeAgentStatus } from '@/features/status/agentStatusStore';
import { normalizeIntelligenceItem } from '@/features/intelligence/types';
import { normalizeToolLog } from '@/features/tools/useToolLogs';
import { getSocket } from '@/shared/realtime/socket';
import { subscribeSocketEvent } from '@/shared/realtime/subscriptions';

export type ActivityFeedRow = {
  id: string;
  kind: string;
  level: string;
  title: string;
  subtitle: string;
  ts: number;
  repeat?: number;
  href?: string;
};

const rows: ActivityFeedRow[] = [];
const listeners = new Set<() => void>();

let socketBound = false;
let sawInitSync = false;
let bridgeConnected = false;
let lastAgentOnline: boolean | null = null;
let lastAgentPaused = false;
let lastDesktopState: string | null = null;
let lastDesktopMode: string | null = null;
let lastLaptopOnline: boolean | null = null;
let lastPcHost: string | null = null;

function notify() {
  for (const listener of listeners) {
    listener();
  }
}

function pushActivityRow(
  row: Omit<ActivityFeedRow, 'id' | 'repeat' | 'ts'> & { id?: string; ts?: number },
) {
  const ts = row.ts ?? Date.now();
  const top = rows[0];
  const sameBurst =
    top &&
    top.kind === row.kind &&
    top.level === row.level &&
    top.title === row.title &&
    ts - top.ts <= (row.level === 'warn' || row.level === 'error' ? 5 * 60_000 : 60_000) &&
    (top.subtitle === row.subtitle || row.level === 'warn' || row.level === 'error');
  if (sameBurst && top) {
    top.repeat = (top.repeat ?? 1) + 1;
    top.ts = ts;
    notify();
    return;
  }

  rows.unshift({
    id: row.id ?? `${row.kind}-${ts}-${Math.random().toString(36).slice(2, 8)}`,
    kind: row.kind,
    level: row.level,
    title: row.title,
    subtitle: row.subtitle,
    ts,
    repeat: 1,
    href: row.href,
  });
  if (rows.length > 200) rows.length = 200;
  notify();
}

export function subscribeActivityFeed(listener: () => void): () => void {
  ensureActivityFeedSubscriptions();
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getActivityFeedRows(): ActivityFeedRow[] {
  return rows;
}

export function useActivityFeedRows(): ActivityFeedRow[] {
  return useSyncExternalStore(subscribeActivityFeed, getActivityFeedRows, getActivityFeedRows);
}

export function pushPersonaFeedRow(persona: PersonaKey, voice: string) {
  const copy = getPersona(persona);
  pushActivityRow({
    kind: 'persona',
    level: 'info',
    title: `${copy.name} is answering`,
    subtitle: `Voice ${voice}`,
    href: '#/agents',
  });
}

export function pushVoiceFeedRow(title: string, subtitle: string, level = 'info') {
  pushActivityRow({ kind: 'voice', level, title, subtitle, href: '#/agents/voice' });
}

export function seedReferenceActivityRows(entries: ActivityFeedRow[]) {
  rows.length = 0;
  for (const entry of entries) {
    rows.push({ ...entry, repeat: entry.repeat ?? 1 });
  }
  notify();
}

function logLevel(raw: Record<string, unknown>): string {
  const level = String(raw.level ?? raw.category ?? '').toUpperCase();
  return level;
}

function handleConfirmResolved(raw: unknown) {
  const item = normalizeConfirm(raw);
  const reason = (item.reason ?? '').toLowerCase();
  if (reason.includes('expir')) {
    pushActivityRow({
      kind: 'approval',
      level: 'neutral',
      title: 'Approval expired, nothing ran',
      subtitle: '',
      href: '#/chat',
    });
    return;
  }
  if (reason.includes('cancel') || reason === 'cancelled') {
    pushActivityRow({
      kind: 'approval',
      level: 'neutral',
      title: 'Cancelled by voice',
      subtitle: '',
      href: '#/chat',
    });
    return;
  }
  if (item.allow) {
    pushActivityRow({
      kind: 'approval',
      level: 'ok',
      title: 'Command approved',
      subtitle: item.tool ?? '',
      href: '#/chat',
    });
    return;
  }
  pushActivityRow({
    kind: 'approval',
    level: 'error',
    title: 'Command denied',
    subtitle: item.tool ?? '',
    href: '#/chat',
  });
}

function handleDesktopStatus(raw: unknown) {
  if (!raw || typeof raw !== 'object') return;
  const row = raw as Record<string, unknown>;
  const state = row.state != null ? String(row.state) : null;
  const online = row.online === true;
  const host = row.host != null ? String(row.host) : lastPcHost ?? 'Laptop';

  if (state === 'panic' && lastDesktopState !== 'panic') {
    pushActivityRow({
      kind: 'device',
      level: 'error',
      title: 'Laptop panic',
      subtitle: '',
      href: '#/agents/laptop',
    });
  } else if (state === 'locked' && lastDesktopState !== 'locked') {
    pushActivityRow({
      kind: 'device',
      level: 'warn',
      title: 'Laptop locked',
      subtitle: '',
      href: '#/agents/laptop',
    });
  } else if (row.mode === 'read' && lastDesktopMode !== 'read') {
    pushActivityRow({
      kind: 'device',
      level: 'warn',
      title: 'Desktop stopped (read-only)',
      subtitle: '',
      href: '#/agents/laptop',
    });
  } else if (online && lastLaptopOnline !== true) {
    pushActivityRow({
      kind: 'device',
      level: 'device',
      title: 'Laptop connected',
      subtitle: `${host} • devices online`,
      href: '#/agents/laptop',
    });
  }

  if (state) lastDesktopState = state;
  if (row.mode != null) lastDesktopMode = String(row.mode);
  if (typeof row.online === 'boolean') lastLaptopOnline = row.online;
}

function handleSystemAlert(raw: unknown) {
  if (!raw || typeof raw !== 'object') return;
  const row = raw as Record<string, unknown>;
  const title = row.title != null ? String(row.title) : 'System alert';
  const severity = String(row.severity ?? row.level ?? 'info').toLowerCase();
  const count = typeof row.count === 'number' ? row.count : typeof row.xcount === 'number' ? row.xcount : 1;
  const level =
    severity === 'critical' || severity === 'error'
      ? 'error'
      : severity === 'warning' || severity === 'warn'
        ? 'warn'
        : 'info';
  pushActivityRow({
    kind: 'alert',
    level,
    title,
    subtitle: count > 1 ? `×${count}, ${severity}` : severity,
    href: '#/activity/timeline',
  });
}

function handleTaskNotification(raw: unknown) {
  if (!raw || typeof raw !== 'object') return;
  const row = raw as Record<string, unknown>;
  const description =
    row.description != null
      ? String(row.description)
      : row.message != null
        ? String(row.message)
        : 'Reminder';
  const spoken = row.spoken === true || row.spoken_aloud === true;
  pushActivityRow({
    kind: 'reminder',
    level: 'info',
    title: description,
    subtitle: spoken ? 'Spoken aloud' : 'Screen only',
    href: '#/activity/tasks',
  });
}

function handleMemoryCleared(raw: unknown) {
  if (!raw || typeof raw !== 'object') return;
  pushActivityRow({
    kind: 'memory',
    level: 'neutral',
    title: 'Short-term memory cleared',
    subtitle: 'Backup saved',
    href: '#/activity/notes',
  });
}

function handleTurnRejected(raw: unknown) {
  if (!raw || typeof raw !== 'object') return;
  const row = raw as Record<string, unknown>;
  const reason = row.reason != null ? String(row.reason) : row.message != null ? String(row.message) : '';
  pushActivityRow({
    kind: 'voice',
    level: 'warn',
    title: 'Voice ignored',
    subtitle: reason,
    href: '#/agents/voice',
  });
}

function handleToolUpdate(raw: unknown) {
  const entry = normalizeToolLog(raw);
  if (!entry) return;
  const status = entry.status.toLowerCase();
  if (status === 'error' || status === 'failed') {
    pushActivityRow({
      kind: 'tool',
      level: 'error',
      title: `${entry.tool} failed`,
      subtitle: entry.result_summary || entry.details || 'Error',
      href: '#/activity/logs',
    });
    return;
  }
  if (status === 'success' || status === 'done' || status === 'completed') {
    const risk = entry.risk?.toLowerCase();
    if (risk === 'medium' || risk === 'high') {
      pushActivityRow({
        kind: 'tool',
        level: 'ok',
        title: `${entry.tool} finished`,
        subtitle: entry.result_summary || '',
        href: '#/activity/logs',
      });
    }
  }
}

function handleIntelligence(raw: unknown) {
  const item = normalizeIntelligenceItem(raw);
  if (!item) return;
  if (item.type === 'image') {
    pushActivityRow({
      kind: 'media',
      level: 'info',
      title: 'JARVIS shared an image',
      subtitle: item.label ?? 'Image',
      href: '#/activity/media',
    });
  }
}

function handleMapFocus(raw: unknown) {
  if (!raw || typeof raw !== 'object') return;
  const row = raw as Record<string, unknown>;
  const label =
    row.display_name != null
      ? String(row.display_name)
      : row.query != null
        ? String(row.query)
        : 'Map focus';
  pushActivityRow({
    kind: 'media',
    level: 'info',
    title: `Map: ${label.split(',')[0]}`,
    subtitle: label,
    href: '#/agents/map',
  });
}

function handleAgentStatus(raw: unknown) {
  const status = normalizeAgentStatus(raw);
  if (status.restarting) {
    pushActivityRow({
      kind: 'system',
      level: 'warn',
      title: 'Agent restarting',
      subtitle: 'Wake it from System',
      href: '#/agents/agent',
    });
  } else if (status.paused && !lastAgentPaused) {
    pushActivityRow({
      kind: 'system',
      level: 'warn',
      title: 'Agent paused',
      subtitle: '',
      href: '#/agents/agent',
    });
  } else if (!status.online && lastAgentOnline === true) {
    pushActivityRow({
      kind: 'system',
      level: 'error',
      title: 'Agent offline',
      subtitle: 'Wake it from System',
      href: '#/agents/agent',
    });
  } else if (status.online && bridgeConnected && lastAgentOnline !== true) {
    pushActivityRow({
      kind: 'system',
      level: 'ok',
      title: 'System online',
      subtitle: 'All systems operational',
      href: '#/agents/agent',
    });
  }
  lastAgentOnline = status.online;
  lastAgentPaused = status.paused;
}

export function ensureActivityFeedSubscriptions(): void {
  if (socketBound) return;
  socketBound = true;
  getSocket();

  subscribeSocketEvent('connect', () => {
    bridgeConnected = true;
  });
  subscribeSocketEvent('disconnect', () => {
    bridgeConnected = false;
  });

  subscribeSocketEvent('init_state', (state: unknown) => {
    if (!state || typeof state !== 'object') return;
    const row = state as Record<string, unknown>;
    if (!sawInitSync) {
      sawInitSync = true;
      pushActivityRow({
        kind: 'sync',
        level: 'neutral',
        title: 'Agent sync complete',
        subtitle: 'JARVIS & ANNA ready',
        href: '#/activity/timeline',
      });
    }
    const agent = row.agent_status;
    if (agent !== undefined) handleAgentStatus(agent);
    const vitals = row.vitals_pc;
    if (vitals && typeof vitals === 'object' && (vitals as { host?: string }).host) {
      lastPcHost = String((vitals as { host?: string }).host);
    }
    if (row.desktop_status) handleDesktopStatus(row.desktop_status);
  });

  subscribeSocketEvent('agent_status', handleAgentStatus);
  subscribeSocketEvent('desktop_status', handleDesktopStatus);
  subscribeSocketEvent('system_alert', handleSystemAlert);
  subscribeSocketEvent('confirm_resolved', handleConfirmResolved);
  subscribeSocketEvent('task_notification', handleTaskNotification);
  subscribeSocketEvent('reminder_due', handleTaskNotification);
  subscribeSocketEvent('memory_cleared', handleMemoryCleared);
  subscribeSocketEvent('turn_rejected', handleTurnRejected);
  subscribeSocketEvent('tool_update', handleToolUpdate);
  subscribeSocketEvent('intelligence_update', handleIntelligence);
  subscribeSocketEvent('map_focus_update', handleMapFocus);

  function ingestErrorLog(data: unknown) {
    if (!data || typeof data !== 'object') return;
    const row = data as Record<string, unknown>;
    const level = logLevel(row);
    if (level !== 'ERROR' && level !== 'CRITICAL') return;
    const text = row.text != null ? String(row.text) : 'Log error';
    pushActivityRow({
      kind: 'log',
      level: 'error',
      title: text,
      subtitle: row.category != null ? String(row.category) : level,
      href: '#/activity/logs',
    });
  }

  subscribeSocketEvent('new_log', ingestErrorLog);
  subscribeSocketEvent('new_logs', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const batch = data as Record<string, unknown>;
    const rawItems = batch.items ?? batch.logs ?? batch.entries;
    if (!Array.isArray(rawItems)) return;
    for (const item of rawItems) ingestErrorLog(item);
  });
}
