import { useMemo } from 'react';
import { AlertTriangle } from 'lucide-react';
import { StatusDot } from '@/shared/ui/Panel';
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

export type AlertsCardProps = {
  systemAlerts: SystemAlert[];
  logs: VortexLog[];
};

export function AlertsCard({ systemAlerts, logs }: AlertsCardProps) {
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
    <section className="activity-alerts n-card edge" data-testid="alerts-card">
      <header className="activity-alerts__hd">
        <h3>Alerts</h3>
        <p>System alerts and WARN+ logs, newest first</p>
      </header>

      <div className="activity-alerts__body">
        {!hasAlerts ? (
          <div className="activity-alerts__normal">
            <StatusDot active tone="accent" />
            <span>
              <strong>Normal.</strong> No alerts.
            </span>
          </div>
        ) : (
          <>
            {systemAlerts.slice(0, 10).map((alert, index) => {
              const count = alert.xcount ?? alert.count ?? 1;
              const lastSeen = formatLastSeen(alert.last_seen ?? alert.ts);
              return (
                <div
                  key={alert.id ?? `${alert.title}-${index}`}
                  className="activity-alert-row"
                  data-testid="system-alert-row"
                >
                  <AlertTriangle size={13} className="activity-alert-row__icon" aria-hidden />
                  <div className="min-w-0">
                    <p className="activity-alert-row__title">
                      <span className="activity-alert-row__sev">
                        {alert.severity || 'alert'}
                      </span>
                      {alert.title}
                    </p>
                    <p className="activity-alert-row__meta">
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
                className="activity-alert-row"
                data-testid="log-alert-row"
                data-level={level}
              >
                <AlertTriangle size={13} className="activity-alert-row__icon" aria-hidden />
                <div className="min-w-0">
                  <span className="activity-alert-row__sev">{level}</span>
                  <span>{log.text}</span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </section>
  );
}

export default AlertsCard;
