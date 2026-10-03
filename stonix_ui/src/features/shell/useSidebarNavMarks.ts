import { useEffect, useMemo, useState } from 'react';
import { useAgentStatus } from '@/features/status/useAgentStatus';
import { useDesktopStatus } from '@/features/status/useDesktopStatus';
import { useSocketEvent } from '@/shared/realtime/subscriptions';
import type { AppRoute } from './router';

type NavAlert = {
  id: string;
  severity: string;
  seen: boolean;
};

const WARN_LEVELS = new Set(['warning', 'warn', 'error', 'critical', 'err']);

function severityBucket(raw: string | undefined): 'info' | 'warn' | 'err' {
  const s = (raw ?? '').toLowerCase();
  if (s === 'error' || s === 'critical' || s === 'err') return 'err';
  if (WARN_LEVELS.has(s)) return 'warn';
  return 'info';
}

function ingestAlert(raw: Record<string, unknown>): NavAlert | null {
  const severity = raw.severity != null ? String(raw.severity) : raw.level != null ? String(raw.level) : 'warning';
  const bucket = severityBucket(severity);
  if (bucket === 'info') return null;
  const id =
    raw.id != null
      ? String(raw.id)
      : `${raw.code ?? raw.title ?? 'alert'}-${raw.ts ?? raw.last_seen ?? Date.now()}`;
  return { id, severity: bucket, seen: false };
}

export function useSidebarNavMarks(route: AppRoute) {
  const agent = useAgentStatus();
  const desktop = useDesktopStatus();
  const [alerts, setAlerts] = useState<NavAlert[]>([]);

  const pushAlert = (row: NavAlert) => {
    setAlerts(prev => {
      const without = prev.filter(a => a.id !== row.id);
      return [row, ...without].slice(0, 40);
    });
  };

  useSocketEvent('system_alert', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const row = ingestAlert(data as Record<string, unknown>);
    if (row) pushAlert(row);
  });

  useSocketEvent('alert', (data: unknown) => {
    if (!data || typeof data !== 'object') return;
    const raw = data as Record<string, unknown>;
    const level = raw.level != null ? String(raw.level) : undefined;
    const row = ingestAlert({ ...raw, severity: level ?? raw.severity });
    if (row) pushAlert(row);
  });

  useEffect(() => {
    if (route.page === 'activity' && (route.tab === 'timeline' || route.tab === undefined)) {
      setAlerts(prev => prev.map(a => ({ ...a, seen: true })));
    }
  }, [route.page, route.tab]);

  return useMemo(() => {
    const unseen = alerts.filter(a => !a.seen);
    const warnCount = unseen.length;
    const activityTone = unseen.some(a => a.severity === 'err') ? ('err' as const) : ('warn' as const);
    const laptopPanic = desktop.seen && (desktop.state === 'panic' || desktop.panic);
    const agentsDot = !agent.online || laptopPanic;
    return {
      activityCount: warnCount,
      activityTone,
      showActivityBadge: warnCount > 0,
      showAgentsDot: agentsDot,
    };
  }, [alerts, agent.online, desktop.panic, desktop.seen, desktop.state]);
}
