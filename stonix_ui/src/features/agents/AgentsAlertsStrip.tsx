import { useMemo } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Panel, StatusDot } from '@/shared/ui/Panel';
import { toEpochMs } from '@/shared/contracts/events';
import type { SystemAlert, VortexLog } from '@/features/command-center/hooks/useCommandCenter';

const ALERT_LEVELS = new Set(['warning', 'error', 'critical']);

function resolveLogLevel(log: VortexLog): string | null {
  const level = log.level?.toLowerCase();
  if (level && ALERT_LEVELS.has(level)) return level.toUpperCase();
  const category = log.category?.toLowerCase();
  if (category && ALERT_LEVELS.has(category)) return category.toUpperCase();
  return null;
}

function formatLastSeen(ts: unknown): string {
  const ms = toEpochMs(ts);
  if (ms === undefined) return '';
  const ago = Math.floor((Date.now() - ms) / 1000);
  return ago >= 0 ? `${ago}s ago` : '';
}

export type AgentsAlertsStripProps = {
  systemAlerts: SystemAlert[];
  logs: VortexLog[];
};

/** Legacy alerts strip on Agents until Activity Timeline (X-108). */
export function AgentsAlertsStrip({ systemAlerts, logs }: AgentsAlertsStripProps) {
  const logAlerts = useMemo(
    () =>
      logs
        .map(log => ({ log, level: resolveLogLevel(log) }))
        .filter((entry): entry is { log: VortexLog; level: string } => entry.level != null)
        .slice(0, 20),
    [logs],
  );

  const hasAlerts = systemAlerts.length > 0 || logAlerts.length > 0;

  return (
    <Panel title="Alerts" className="agents-alerts-legacy min-h-0" data-legacy>
      <div className="space-y-2">
        {!hasAlerts ? (
          <div className="empty-compact">
            <StatusDot active tone="accent" />
            Normal
          </div>
        ) : (
          <>
            {systemAlerts.slice(0, 10).map((alert, index) => {
              const count = alert.xcount ?? alert.count ?? 1;
              const lastSeen = formatLastSeen(alert.last_seen ?? alert.ts);
              return (
                <div
                  key={alert.id ?? `${alert.title}-${index}`}
                  className="alert-row"
                  data-testid="system-alert-row"
                >
                  <AlertTriangle size={13} className="mt-0.5 text-command-warning" />
                  <div className="min-w-0">
                    <p className="font-semibold text-command-text">
                      <span className="mr-2 uppercase text-[10px] text-command-warning">
                        {alert.severity || 'alert'}
                      </span>
                      {alert.title}
                    </p>
                    <p className="text-[10px] text-command-muted">
                      {count > 1 ? `×${count}` : null}
                      {count > 1 && lastSeen ? ' · ' : null}
                      {lastSeen ? `last ${lastSeen}` : null}
                    </p>
                  </div>
                </div>
              );
            })}
            {logAlerts.slice(0, 10).map(({ log, level }, index) => (
              <div
                key={`${log.timestamp}-${index}`}
                className="alert-row"
                data-testid="log-alert-row"
                data-level={level}
              >
                <AlertTriangle size={13} className="mt-0.5 text-command-warning" />
                <div className="min-w-0">
                  <span className="mr-2 text-[10px] font-semibold uppercase text-command-warning">
                    {level}
                  </span>
                  <span>{log.text}</span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </Panel>
  );
}

export default AgentsAlertsStrip;
